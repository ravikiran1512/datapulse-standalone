"""Official partner-directory discovery + bounded ATS dry-run.

The public directory is *evidence*, not a licence to infer a partner tier.
Only configured employer career pages are visited. ATS slugs come from links
on those pages or a previously verified board registry, never company names.
No function in this module imports the database client or writes jobs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from datapulse_ingestion.connectors import CONNECTORS, Fetcher
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import (
    SAP_BASIS_ROLE, SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR,
    country_of, combine_structured_experience, parse_experience,
    normalize_for_profiles,
)
from datapulse_ingestion.policy import ROLE, SENIOR

ROOT = Path(__file__).resolve().parents[3]
SAP_DIRECTORY = "https://partnerfinder.sap.com/partnerNavigator"
DATABRICKS_DIRECTORY = "https://www.databricks.com/partners/partner-directory"
STUDENT_ROLE = re.compile(
    r"\b(?:werkstudent(?:in)?|working[\s-]*student|studentische[\s-]+aushilfe|"
    r"intern(?:ship)?|praktik(?:um|ant|antin))\b", re.I)
EXECUTIVE_ROLE = re.compile(
    r"\b(?:director|head\s+of|chief|vp|vice\s+president|executive|"
    r"principal|staff|managing\s+partner)\b", re.I)
SAP_SYSTEM_ROLE = re.compile(
    r"\bsap\s+(?:systems?|systembetreuung|administrator)\b", re.I)
ATS_HOSTS = (
    ".jobs.personio.de", ".jobs.personio.com", ".greenhouse.io",
    ".lever.co", ".ashbyhq.com", ".myworkdayjobs.com",
    ".smartrecruiters.com", ".softgarden.io", ".softgarden.de",
    ".successfactors.com",
)


def safe_https(url: str) -> bool:
    p = urlsplit(url)
    return (p.scheme == "https" and bool(p.hostname) and not p.username
            and not p.password and p.port in (None, 443)
            and not re.search(r"(^|\.)localhost$|\.local$|\.internal$", p.hostname or ""))


def canonical_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def directory_url(ecosystem: str, name: str) -> str:
    if ecosystem == "sap":
        return SAP_DIRECTORY + "?q=" + quote(name)
    if ecosystem == "databricks":
        return DATABRICKS_DIRECTORY
    raise ValueError("Only SAP and Databricks directories are supported")


def directory_evidence(html: str, name: str) -> bool:
    """Text visibility only. It never asserts certification or tier."""
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    needle = canonical_name(name)
    return bool(needle and len(needle) > 4 and any(
        needle in canonical_name(node.get_text(" ", strip=True))
        for node in soup.select("a, article, h2, h3") if len(node.get_text(" ", strip=True)) < 260
    ))


def ats_board_from_link(url: str, company: str) -> dict | None:
    """Derive a board token ONLY from a real, explicit ATS URL."""
    if not safe_https(url):
        return None
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    base = {"company": company, "enabled": True, "discovered_from": url}
    if host.endswith((".jobs.personio.de", ".jobs.personio.com")):
        token = host.split(".")[0]
        if token in ("www", "jobs") or not re.fullmatch(r"[a-z0-9-]{2,65}", token):
            return None
        return {**base, "type": "personio", "board": token,
                "apply_url_template": "https://" + host + "/job/{id}?language=en"}
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io",
                "boards-api.greenhouse.io"):
        token = parts[2] if host == "boards-api.greenhouse.io" and len(parts) > 2 and parts[:2] == ["v1", "boards"] else (parts[0] if parts else "")
        if re.fullmatch(r"[a-zA-Z0-9_-]{2,70}", token):
            return {**base, "type": "greenhouse", "board": token}
    if host in ("jobs.lever.co", "api.lever.co", "api.eu.lever.co"):
        token = parts[2] if host != "jobs.lever.co" and len(parts) > 2 and parts[:2] == ["v0", "postings"] else (parts[0] if parts and host == "jobs.lever.co" else "")
        if re.fullmatch(r"[a-zA-Z0-9_-]{2,70}", token):
            return {**base, "type": "lever", "board": token,
                    "region": "eu" if host == "api.eu.lever.co" else "global",
                    "max_pages": 2}
    if host == "jobs.ashbyhq.com" and parts:
        if re.fullmatch(r"[a-zA-Z0-9_-]{2,70}", parts[0]):
            return {**base, "type": "ashby", "board": parts[0]}
    if host == "jobs.smartrecruiters.com" and parts:
        if re.fullmatch(r"[a-zA-Z0-9_-]{2,70}", parts[0]):
            return {**base, "type": "smartrecruiters", "board": parts[0],
                    "queries": ["SAP Basis", "Data Engineer"], "max_pages_per_query": 1}
    if host.endswith(".myworkdayjobs.com"):
        site = next((part for part in parts if part.lower() not in
                     ("en-us", "en-gb", "de-de", "de", "en", "job")), "")
        if re.fullmatch(r"[a-zA-Z0-9_-]{2,80}", site):
            return {**base, "type": "workday",
                    "career_url": "https://" + host + "/en-US/" + site,
                    "queries": ["SAP Basis", "Data Engineer"],
                    "max_pages_per_query": 1}
    # Softgarden and SuccessFactors need feed-specific schema verification.
    # Never turn an ordinary careers HTML page into a supposed JSON endpoint.
    return None


def ats_links(html: str, base: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    seen = set()
    rows = []
    for element in soup.select("a[href], iframe[src]")[:350]:
        href = element.get("href") or element.get("src")
        url = urljoin(base, href or "")
        if not safe_https(url):
            continue
        board = ats_board_from_link(url, "pending")
        if board:
            key = (board["type"], board.get("board", board.get("career_url")))
            if key not in seen:
                seen.add(key)
                rows.append({"url": url, "type": board["type"],
                             "board": board.get("board"), "career_url": board.get("career_url")})
        else:
            host = urlsplit(url).hostname or ""
            if host.endswith(ATS_HOSTS) and url not in seen:
                seen.add(url)
                rows.append({"url": url, "type": "unsupported_or_unverified"})
        if len(rows) >= 12:
            break
    return rows


def strict_partner_profiles(raw: RawJob) -> tuple[tuple[str, ...], str, dict]:
    """Hard rules specific to partner jobs; no inferred zero years."""
    country = country_of(raw)
    if country not in ("DE", "IN"):
        return (), "country_unverified_or_outside_scope", {}
    exp = combine_structured_experience(raw, parse_experience(raw.description))
    title = raw.job_title or ""
    contract = (raw.employment_type or "") + " " + title
    details = {"country": country, "min_experience_years": exp.minimum,
               "max_experience_years": exp.maximum,
               "experience_evidence": exp.evidence}
    if EXECUTIVE_ROLE.search(title):
        return (), "executive_or_principal_title", details
    is_basis = bool(SAP_BASIS_ROLE.search(title) or
                    (SAP_SYSTEM_ROLE.search(title) and STUDENT_ROLE.search(contract)))
    if is_basis:
        student = bool(STUDENT_ROLE.search(contract))
        if student:
            return ((SAP_STUDENT,), "accepted_student", details) if country == "DE" else (
                (), "sap_student_non_germany", details)
        # Unknown / open-ended 5+ years is review, not a proven max <= 7.
        if exp.minimum is None or exp.maximum is None:
            return (), "sap_experience_range_unverified", details
        if 4 <= exp.minimum <= exp.maximum <= 7:
            return (SAP_EXPERIENCED,), "accepted_experienced", details
        return (), "sap_experience_outside_4_to_7", details
    if not ROLE.search(title):
        return (), "unrelated_title", details
    if country != "DE":
        return (), "data_engineering_non_germany", details
    if SENIOR.search(title) or re.search(r"\b(?:lead|manager)\b", title, re.I):
        return (), "senior_data_engineering", details
    if exp.minimum is None or exp.maximum is None:
        # Student/intern contracts are the only no-years exception.
        if STUDENT_ROLE.search(contract) and (exp.minimum is None):
            return (DE_JUNIOR,), "accepted_student_no_required_years", details
        return (), "de_experience_unverified", details
    if exp.minimum >= 3 or exp.maximum >= 3:
        return (), "de_requires_3_plus_years", details
    # Reuse existing language, tech relevance, role fit and Germany checks.
    normalized, reason = normalize_for_profiles(raw)
    if normalized is None or DE_JUNIOR not in normalized.persona_tags:
        return (), "de_original_quality_filter_" + reason, details
    return (DE_JUNIOR,), "accepted_de_junior", details


def select_targets(rows: list[dict], ecosystems: set[str], maximum: int) -> list[dict]:
    """Interleave ecosystems instead of selecting SAP-only first N."""
    bins = {key: [r for r in rows if r.get("ecosystem") == key]
            for key in ("sap", "databricks") if key in ecosystems}
    result = []
    while len(result) < maximum and any(bins.values()):
        for key in bins:
            if bins[key] and len(result) < maximum:
                result.append(bins[key].pop(0))
    return result


async def robots_ok(client: httpx.AsyncClient, url: str, cache: dict) -> bool:
    if not safe_https(url):
        return False
    p = urlsplit(url)
    root = p.scheme + "://" + p.netloc
    if root not in cache:
        try:
            r = await client.get(root + "/robots.txt", timeout=8, follow_redirects=False)
            rules = RobotFileParser()
            if r.status_code != 200 or "user-agent:" not in r.text.casefold():
                cache[root] = None
            else:
                rules.parse(r.text.splitlines())
                cache[root] = rules
        except httpx.HTTPError:
            cache[root] = None
    rules = cache[root]
    return rules is not None and rules.can_fetch("DataPulseJobs", url)


async def get_html(client: httpx.AsyncClient, url: str, cache: dict) -> tuple[str | None, str]:
    if not await robots_ok(client, url, cache):
        return None, "robots_unverified_or_disallowed"
    try:
        r = await client.get(url, timeout=12, follow_redirects=False)
        if r.status_code != 200:
            return None, "http_" + str(r.status_code)
        if len(r.content) > 1_500_000:
            return None, "oversized"
        if not any(t in r.headers.get("content-type", "").casefold()
                   for t in ("text/html", "text/xml", "application/xhtml")):
            return None, "not_html"
        return r.text, "ok"
    except httpx.HTTPError as e:
        return None, type(e).__name__


async def scan_partner_targets(
    targets: list[dict], verified_boards: list[dict], *, ecosystems: set[str],
    max_partners: int = 6, max_feeds: int = 3,
    profiles: tuple[str, ...] | None = None,
    request_client: httpx.AsyncClient | None = None,
    connector_fetcher: Fetcher | None = None,
) -> dict:
    own_client = request_client is None
    own_fetcher = connector_fetcher is None
    client = request_client or httpx.AsyncClient(
        headers={"User-Agent": "DataPulseJobs/0.1 (partner directory read-only)"},
        follow_redirects=False, timeout=12)
    fetcher = connector_fetcher or Fetcher(timeout=12, delay_seconds=1.2)
    cache: dict = {}
    selected = select_targets(targets, ecosystems, max_partners)
    profile_selection = profiles or (SAP_EXPERIENCED, SAP_STUDENT, DE_JUNIOR)
    summary = {"dry_run": True, "source": "partner_directories",
               "target_profiles": list(profile_selection),
               "ecosystems": sorted(ecosystems), "targets_checked": len(selected),
               "directory_names_visible": 0, "feeds_validated": 0,
               "jobs_seen": 0, "accepted": 0, "per_profile": {p: 0 for p in
                  profile_selection},
               "rejected": {}, "companies": [], "samples": [],
               "inserted": 0, "updated": 0,
               "notes": "Public directory visibility does not establish current certification tier."}
    rejections = Counter()
    try:
        for target in selected:
            name = target["name"]
            ecosystem = target["ecosystem"]
            info = {"company": name, "ecosystem": ecosystem,
                    "target_country": target.get("country"),
                    "directory_status": "unverified", "tier": None,
                    "ats_candidates": [], "feed_status": "unverified",
                    "jobs_seen": 0, "accepted": 0}
            directory = directory_url(ecosystem, name)
            html, status = await get_html(client, directory, cache)
            info["directory_status"] = ("name_visible_on_public_page" if html is not None and
                                         directory_evidence(html, name) else
                                         "not_visible_or_dynamic" if html is not None else status)
            info["directory_url"] = directory
            if info["directory_status"] == "name_visible_on_public_page":
                summary["directory_names_visible"] += 1
            career = target.get("career_page", "")
            if safe_https(career):
                page, status = await get_html(client, career, cache)
                info["career_status"] = status
                if page:
                    info["ats_candidates"] = ats_links(page, career)
                elif ats_board_from_link(career, name):
                    info["ats_candidates"] = [{"url": career,
                                               "type": ats_board_from_link(career, name)["type"],
                                               "evidence": "configured ATS career link"}]
            candidates = []
            # Previously confirmed ATS boards remain usable as technical feeds
            # even if a JS directory doesn't reveal the partner on first page.
            for v in verified_boards:
                if v.get("enabled") and canonical_name(v.get("company", "")) == canonical_name(name):
                    candidates.append((dict(v), "prior_verified_registry"))
            for found in info["ats_candidates"]:
                board = ats_board_from_link(found["url"], name)
                if board and not any(x[0].get("type") == board.get("type") and
                                     x[0].get("board", x[0].get("career_url")) ==
                                     board.get("board", board.get("career_url"))
                                     for x in candidates):
                    candidates.append((board, "discovered_employer_career_link"))
            for board, origin in candidates[:1]:
                if summary["feeds_validated"] >= max_feeds:
                    info["feed_status"] = "skipped_feed_limit"
                    break
                if board["type"] not in CONNECTORS:
                    info["feed_status"] = "connector_not_supported"
                    continue
                try:
                    jobs = await asyncio.to_thread(CONNECTORS[board["type"]], board, fetcher)
                    info["feed_status"] = "public_feed_validated"
                    info["feed_origin"] = origin
                    info["jobs_seen"] = len(jobs)
                    summary["feeds_validated"] += 1
                    summary["jobs_seen"] += len(jobs)
                    for raw in jobs:
                        matched, why, exp = strict_partner_profiles(raw)
                        matched = tuple(p for p in matched if p in profile_selection)
                        if not matched:
                            rejections[why] += 1
                            continue
                        info["accepted"] += 1
                        summary["accepted"] += 1
                        for p in matched:
                            summary["per_profile"][p] += 1
                        if len(summary["samples"]) < 8:
                            summary["samples"].append({
                                "company": raw.company_name, "title": raw.job_title,
                                "country": exp.get("country"), "profiles": list(matched),
                                "experience_min": exp.get("min_experience_years"),
                                "experience_max": exp.get("max_experience_years"),
                                "apply_url": raw.job_url,
                                "directory_status": info["directory_status"]})
                except Exception as exc:
                    info["feed_status"] = "validation_failed"
                    info["feed_error"] = type(exc).__name__
            summary["companies"].append(info)
            await asyncio.sleep(0.2)
    finally:
        if own_client:
            await client.aclose()
        if own_fetcher:
            fetcher.client.close()
    summary["rejected"] = dict(rejections)
    return summary


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only official partner directory + ATS discovery")
    p.add_argument("--targets", type=Path, default=ROOT / "config/partner_directory_targets.json")
    p.add_argument("--boards", type=Path, default=ROOT / "config/verified-boards.json")
    p.add_argument("--ecosystems", default="sap,databricks")
    p.add_argument("--max-partners", type=int, default=6)
    p.add_argument("--max-feeds", type=int, default=3)
    p.add_argument("--dry-run", default="true", choices=["true"])
    args = p.parse_args()
    ecosystems = {x.strip().lower() for x in args.ecosystems.split(",")}
    if not ecosystems or not ecosystems <= {"sap", "databricks"}:
        p.error("ecosystems must be sap,databricks or a subset")
    registry = json.loads(args.targets.read_text(encoding="utf-8"))
    boards = json.loads(args.boards.read_text(encoding="utf-8"))
    result = asyncio.run(scan_partner_targets(
        registry["targets"], boards["boards"], ecosystems=ecosystems,
        max_partners=max(0,min(args.max_partners,20)),
        max_feeds=max(0,min(args.max_feeds,5))))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
