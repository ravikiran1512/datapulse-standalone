'use client';
import { useCallback,useEffect,useMemo,useState } from 'react';
import {Bookmark,BookmarkCheck,ArrowUpRight,Search,SlidersHorizontal,LayoutList,Download,ClipboardCheck,ChartNoAxesCombined,
  LoaderCircle,MapPin,Clock3,ArrowRight,DatabaseZap,RefreshCcw,X,BriefcaseBusiness,
  BadgeCheck,Check,Building2,ExternalLink,ShieldCheck,Heart,CodeXml} from 'lucide-react';
import {FilterDropdown} from '../components/FilterDropdown';
import type {Job,SearchPage} from '../lib/types';

const API=(process.env.NEXT_PUBLIC_API_BASE_URL||'').replace(/\/$/,'');
const germanyRegions=[['','All regions'],['Berlin','Berlin'],['Hamburg','Hamburg'],['Munich','Munich'],
 ['Frankfurt','Frankfurt'],['Cologne','Cologne'],['Magdeburg','Magdeburg'],['Leipzig','Leipzig'],
 ['Dresden','Dresden'],['Stuttgart','Stuttgart'],['Düsseldorf','Düsseldorf'],['Remote','Remote Germany']].map(([value,label])=>({value,label}));
const indiaRegions=[['','All regions'],['Hyderabad','Hyderabad'],['Bengaluru','Bengaluru'],['Pune','Pune'],['Mumbai','Mumbai'],['Delhi','Delhi NCR'],['Chennai','Chennai'],['Kolkata','Kolkata'],['Remote','Remote India']].map(([value,label])=>({value,label}));
const modes=[['','All work modes'],['Remote','Remote'],['Hybrid','Hybrid'],['On-site','On-site']].map(([value,label])=>({value,label}));
const roles=[['','All role types'],['SAP Basis','SAP Basis'],['Data Engineering','Data Engineering'],['AI Engineering','AI Engineering'],['MLOps','MLOps']].map(([value,label])=>({value,label}));
const levels=[['','All levels'],['Working Student','Working Student'],['Internship','Internship'],['Junior','Junior'],['Mid','Mid-level']].map(([value,label])=>({value,label}));
const skillOptions=['SAP Basis','SAP NetWeaver','SAP HANA','SAP S/4HANA','SAP Solution Manager','Linux','SAP BTP','Python','SQL','PySpark','Databricks','Apache Spark','Delta Lake','Azure','ADLS Gen2','AWS','dbt','Airflow','Snowflake','SAP'];
type PersonaId='sap_basis_experienced'|'sap_basis_student'|'data_engineering_junior';
type TrackId='sap_basis'|'data_engineering';
const trackPersonas:Record<TrackId,PersonaId[]>={
 sap_basis:['sap_basis_experienced','sap_basis_student'],
 data_engineering:['data_engineering_junior'],
};
type ApplicationStatus='Saved'|'Applied'|'Interviewing'|'Archived';
const lifecycleStatuses:ApplicationStatus[]=['Saved','Applied','Interviewing','Archived'];
function csvValue(value:unknown){
 const text=String(value??'');
 // Prefix spreadsheet formula-looking cells, then escape CSV quotes.
 const safe=/^[\s]*[=+@-]/.test(text)?"'"+text:text;
 return '"'+safe.replace(/"/g,'""')+'"';
}
function makeSavedCsv(jobs:Job[],statuses:Record<string,ApplicationStatus>){
 const fields=['Status','Job Title','Company','Country','Location','Experience','Fit Score','Source','Apply URL','Salary','Visa Support','Skills'];
 const lines=jobs.map(j=>[
  statuses[j.id]||'Saved',j.job_title,j.company_name,j.country_code||'',j.location,
  j.min_experience_years===null?'Experience not specified':String(j.min_experience_years)+' years',
  Math.round(j.match_score*100)+'%',j.source_name,j.official_apply_url,
  j.salary_range||'',j.visa_supported?'Explicit sponsorship mentioned':'Not verified',j.tech_stack.join('; ')
 ].map(csvValue).join(','));
 return '\ufeff'+[fields.map(csvValue).join(','),...lines].join('\r\n');
}

const personaOptions:{id:TrackId,label:string,description:string,scope:string}[]=[
 {id:'sap_basis',label:'SAP Basis',description:'Up to 5 years required, or experience not specified · All employment types',scope:'Germany + India'},
 {id:'data_engineering',label:'Data Engineering',description:'Up to 2 years required, or experience not specified · All employment types',scope:'Germany + India'},
];
const countryOptions=[{value:'',label:'Both countries'},{value:'DE',label:'Germany'},{value:'IN',label:'India'}];
const personaNames:Record<PersonaId,string>={sap_basis_experienced:'SAP Basis · Up to 5 years',sap_basis_student:'SAP Basis · Student / Entry',data_engineering_junior:'Data Engineering · Up to 2 years'};
function experienceText(j:Job){if(j.min_experience_years===null)return 'Experience not specified';if(j.max_experience_years!==null&&j.max_experience_years!==j.min_experience_years)return `${j.min_experience_years}–${j.max_experience_years} years`;return j.max_experience_years===j.min_experience_years?`${j.min_experience_years} years required`:`${j.min_experience_years}+ years required`;}
function languageText(j:Job){const value=(j.language_required||'').trim();if(!value||value==='Unknown'||value==='Not specified')return 'Language not specified';if(value==='Bilingual')return j.is_english_friendly?'English & German mentioned':'German required';if(value==='German')return 'German mentioned';if(value==='English')return 'English mentioned';return value;}
function dateText(s:string|null){if(!s)return 'Date not provided';const d=new Date(s+'T12:00:00');return Number.isNaN(d.getTime())?'Date not provided':d.toLocaleDateString('en-DE',{month:'short',day:'numeric',year:'numeric'})}
function Fit({score,domain,onClick}:{score:number,domain?:string,onClick?:()=>void}){const p=Math.round(score*100);const contents=<><span className="fit-number">{p}%</span><span className="fit-label">{domain==='SAP Basis'?'role fit':'tech fit'}</span></>;
 if(onClick)return <button type="button" className={'fit fit-action '+(p>=80?'fit-high':p>=70?'fit-mid':'fit-muted')} aria-label={'View fit analysis: '+p+' percent'} onClick={onClick}>{contents}</button>;
 return <div className={'fit '+(p>=80?'fit-high':p>=70?'fit-mid':'fit-muted')}>{contents}</div>;
}
function Chip({children,accent}:{children:React.ReactNode,accent?:boolean}){return <span className={'chip '+(accent?'chip-accent':'')}>{children}</span>}

export default function Page(){
 const [tab,setTab]=useState<'jobs'|'saved'>('jobs');
 const [query,setQuery]=useState(''); const [debounced,setDebounced]=useState('');
 const [personas,setPersonas]=useState<TrackId[]>([]); const [country,setCountry]=useState('');
 const [region,setRegion]=useState(''); const [mode,setMode]=useState(''); const [role,setRole]=useState('');const [level,setLevel]=useState('');
 const [english,setEnglish]=useState(false);const [fitOnly,setFitOnly]=useState(false);
 const [skills,setSkills]=useState<string[]>([]);const [skillsOpen,setSkillsOpen]=useState(false);
 const [order,setOrder]=useState<'newest'|'match'>('newest'); const [page,setPage]=useState(1);
 const [result,setResult]=useState<SearchPage|null>(null);const [loading,setLoading]=useState(false);
 const [error,setError]=useState(''); const [selected,setSelected]=useState<Job|null>(null);
 const [fitJob,setFitJob]=useState<Job|null>(null);
 const [applicationStatus,setApplicationStatus]=useState<Record<string,ApplicationStatus>>({});
 const [saved,setSaved]=useState<string[]>([]);const [savedJobs,setSavedJobs]=useState<Record<string,Job>>({});const [hydrated,setHydrated]=useState(false);
 const [reload,setReload]=useState(0);
 useEffect(()=>{try{const v=localStorage.getItem('datapulse-standalone-saved-v2');if(v)setSaved(JSON.parse(v));const details=localStorage.getItem('datapulse-standalone-saved-details-v2');if(details)setSavedJobs(JSON.parse(details));const statuses=localStorage.getItem('datapulse-standalone-app-status-v1');if(statuses)setApplicationStatus(JSON.parse(statuses))}catch{}setHydrated(true)},[]);
 useEffect(()=>{if(hydrated){localStorage.setItem('datapulse-standalone-saved-v2',JSON.stringify(saved));localStorage.setItem('datapulse-standalone-saved-details-v2',JSON.stringify(savedJobs));localStorage.setItem('datapulse-standalone-app-status-v1',JSON.stringify(applicationStatus))}},[saved,savedJobs,applicationStatus,hydrated]);
 useEffect(()=>{const timer=setTimeout(()=>setDebounced(query),330);return()=>clearTimeout(timer)},[query]);
 useEffect(()=>setPage(1),[debounced,personas,country,region,mode,role,level,english,fitOnly,skills,order]);
 useEffect(()=>{
  if(!API){setError('Set NEXT_PUBLIC_API_BASE_URL to connect your new DataPulse backend.');return}
  const controller=new AbortController();let mounted=true;
  const params=new URLSearchParams({page:String(page),limit:'20',english_only:String(english),
    min_match_score:fitOnly?'0.70':'0',order});
  personas.flatMap(x=>trackPersonas[x]).forEach(x=>params.append('persona',x));if(country)params.set('country',country);
  if(debounced.trim())params.set('query',debounced.trim());
  if(region)params.set('region',region);if(mode)params.set('work_mode',mode);
  if(role)params.set('role_type',role);if(level)params.set('level',level);
  skills.forEach(x=>params.append('skills',x));
  setLoading(true);setError('');
  fetch(`${API}/api/v1/jobs?${params}`,{signal:controller.signal}).then(async r=>{
    if(!r.ok){let detail='';try{detail=(await r.json()).detail||''}catch{}throw new Error(detail||`API returned ${r.status}`)}
    return r.json() as Promise<SearchPage>;
  }).then(data=>{if(mounted){setResult(data);setSelected(old=>old?data.items.find(x=>x.id===old.id)||null:null)}})
    .catch(e=>{if(mounted&&e.name!=='AbortError')setError(e.message||'Unable to connect to the API')})
    .finally(()=>{if(mounted)setLoading(false)});
  return()=>{mounted=false;controller.abort()};
 },[debounced,personas,country,region,mode,role,level,english,fitOnly,skills,page,order,reload]);
 const toggleSaved=useCallback((id:string,job?:Job)=>{setSaved(v=>v.includes(id)?v.filter(x=>x!==id):[...v,id]);setSavedJobs(v=>{const n={...v};if(n[id])delete n[id];else if(job)n[id]=job;return n});},[]);
 const jobs=result?.items||[];
 const exportSaved=()=>{
  const list=Object.values(savedJobs).filter(j=>saved.includes(j.id));
  if(!list.length)return;
  const file=new Blob([makeSavedCsv(list,applicationStatus)],{type:'text/csv;charset=utf-8;'});
  const url=URL.createObjectURL(file);const a=document.createElement('a');
  a.href=url;a.download='datapulse_saved_jobs_'+new Date().toISOString().slice(0,10)+'.csv';a.click();
  setTimeout(()=>URL.revokeObjectURL(url),500);
 };
 const fitDetails=(j:Job)=>j.fit_breakdown||{
  matched_skills:j.tech_stack,missing_or_recommended_skills:[],
  experience_fit:'See employer requirements',method:'Technical tags only; not an uploaded resume comparison'};
 const visible=tab==='jobs'?jobs:Object.values(savedJobs).filter(j=>saved.includes(j.id));
 const totalPages=Math.ceil((result?.total||0)/20);
 const reset=()=>{setQuery('');setPersonas([]);setRegion('');setCountry('');setMode('');setRole('');setLevel('');setSkills([]);setEnglish(false);setFitOnly(false)};
 const togglePersona=(id:TrackId)=>setPersonas(selected=>selected.includes(id)?selected.filter(x=>x!==id):[...selected,id]);
 const regions=country==='IN'?indiaRegions:country==='DE'?germanyRegions:[...germanyRegions,...indiaRegions.filter(x=>x.value&&!germanyRegions.some(y=>y.value===x.value))];
 return <div className="shell">
  <header className="nav">
   <div className="brand" onClick={()=>setTab('jobs')} role="button" tabIndex={0} onKeyDown={e=>{if(e.key==='Enter')setTab('jobs')}}>
    <span className="brand-icon"><DatabaseZap size={20} strokeWidth={2.2}/></span>
    <span>data<span className="brand-green">pulse</span></span>
   </div>
   <nav className="nav-links" aria-label="Main navigation">
    <button className={'nav-tab '+(tab==='jobs'?'active':'')} onClick={()=>setTab('jobs')}><LayoutList size={16}/> Jobs</button>
    <button className={'nav-tab '+(tab==='saved'?'active':'')} onClick={()=>setTab('saved')}><Bookmark size={16}/> Saved <span className="nav-count">{saved.length}</span></button>
   </nav>
   <span className="nav-end"><span className="online-dot"/> Germany + India · Two career tracks</span>
  </header>
  <main className="container">
   <div className="intro"><div className="eyebrow"><span className="eyebrow-line"/> BUILT FOR SAP BASIS & DATA ENGINEERING</div>
    <h1>One search. <span>Two career paths.</span></h1>
    <p>Explore SAP Basis and Data Engineering roles in Germany and India. Experience up to 5 or 2 years—or not specified—is welcome; technical relevance ranks results instead of hiding them.</p>
   </div>
   <section aria-label="Select job search profiles" className="profiles-area">
    <div className="profile-head"><div><strong>Profile presets</strong><p>Select one or combine multiple profiles to search across career tracks.</p></div><span className="profile-count">{personas.length===0?"Show All":`${personas.length} selected`}</span></div>
    <div className="profile-grid">{personaOptions.map(opt=><button type="button" key={opt.id} aria-pressed={personas.includes(opt.id)}
      className={'profile-option '+(personas.includes(opt.id)?'profile-active':'')}
      onClick={()=>togglePersona(opt.id)}>
      <div className="profile-row"><span className="profile-check">{personas.includes(opt.id)?<Check size={14}/>:null}</span><strong>{opt.label}</strong></div>
      <span className="profile-desc">{opt.description}</span><span className="profile-scope"><MapPin size={12}/>{opt.scope}</span>
    </button>)}</div>
    <p className="profiles-note">No category selected shows both career tracks. Unknown experience stays labeled “Experience not specified”—it is never treated as confirmed zero years.</p>
   </section>
   <section aria-label="Find jobs" className="search-area">
    <div className="search-input-wrap"><Search className="search-icon" size={23}/>
      <input aria-label="Search role, company, skill or city" value={query} onChange={e=>setQuery(e.target.value)}
        placeholder="Search role, company, skill or city"/>
      {query&&<button className="clear-search" title="Clear search" onClick={()=>setQuery('')}><X size={18}/></button>}
    </div>
    <div className="filter-row">
     <FilterDropdown label="Country" value={country} options={countryOptions} onChange={v=>{setCountry(v);setRegion('')}}/>
     <FilterDropdown label="Region" value={region} options={regions} onChange={setRegion}/>
     <FilterDropdown label="Work mode" value={mode} options={modes} onChange={setMode}/>
     <FilterDropdown label="Role type" value={role} options={roles} onChange={setRole}/>
     <FilterDropdown label="Level" value={level} options={levels} onChange={setLevel}/>
    </div>
    <div className="search-helpers">
     <div className="pills"><button className={'pill '+(english?'pill-on':'')} onClick={()=>setEnglish(v=>!v)}>
       {english&&<Check size={13}/>} English-friendly</button>
      <button className={'pill '+(fitOnly?'pill-on':'')} onClick={()=>setFitOnly(v=>!v)}>
       {fitOnly&&<Check size={13}/>} Optional: 70%+ relevance</button>
      <button className={'pill '+(skillsOpen?'pill-muted-on':'')} onClick={()=>setSkillsOpen(v=>!v)}><SlidersHorizontal size={14}/> Skills {skills.length>0&&<span className="skills-count">{skills.length}</span>}</button>
     </div>
     <span className="boolean-note">Tip: try <code>"Data Engineer" AND PySpark</code></span>
    </div>
    {skillsOpen&&<div className="skills-panel"><div className="skills-header"><strong>Technical skills</strong><button onClick={()=>setSkills([])}>Clear selection</button></div>
      <div className="skills-grid">{skillOptions.map(s=><button key={s} className={'skill '+(skills.includes(s)?'chosen':'')} onClick={()=>setSkills(old=>old.includes(s)?old.filter(x=>x!==s):[...old,s])}>
       {skills.includes(s)&&<Check size={14}/>} {s}</button>)}</div>
      <p>Skills are optional filters. The technical relevance score ranks jobs and is not a mandatory qualification threshold.</p>
     </div>}
   </section>
   <div className="results-header"><div><div className="results-title">{tab==='jobs'?'Latest opportunities':'Your saved jobs'}</div>
     <div className="results-sub">{tab==='saved'?'Bookmarks are kept in this browser until account sign-in is configured.':
       result?`${result.total.toLocaleString()} matching roles ${personas.length===0?'across all profiles':'within selected profiles'}`:'Direct ATS + company career portals'}</div></div>
      <div className="results-tools">{tab==='saved'&&<button type="button" className="export-csv" disabled={saved.length===0} onClick={exportSaved}><Download size={15}/> Export Saved Jobs</button>}<button className="refresh" onClick={()=>setReload(x=>x+1)} title="Reload jobs"><RefreshCcw size={16}/></button>
       <select aria-label="Sort jobs" value={order} onChange={e=>setOrder(e.target.value as 'newest'|'match')}>
        <option value="newest">Newest first</option><option value="match">Best match</option>
       </select></div>
   </div>
   <div className="content-layout"><div className="job-list">
    {error&&<div className="info-state"><div className="info-icon"><DatabaseZap size={26}/></div>
       <h3>{!API?'Your standalone backend is not connected yet':'Could not load jobs'}</h3>
       <p>{error}</p><p className="info-note">Once PostgreSQL, FastAPI, and Vercel are configured, verified jobs will populate here automatically. No sample jobs are shown as live vacancies.</p>
       {API&&<button className="retry" onClick={()=>setReload(n=>n+1)}>Try again <ArrowRight size={15}/></button>}
     </div>}
    {!error&&loading&&<div className="info-state"><LoaderCircle className="spinning" size={27}/><h3>Searching verified job boards</h3><p>Loading roles from DataPulse API…</p></div>}
    {!error&&!loading&&visible.length===0&&<div className="info-state"><Search size={26}/><h3>{tab==='saved'?'No saved jobs yet':'No matching opportunities found'}</h3>
       <p>{tab==='saved'?'Save a job to revisit it here. Saved jobs stay in this browser even when you change search filters.':'Try both countries, another region, or turn off an optional filter.'}</p>
       <button className="retry" onClick={reset}>Reset filters <ArrowRight size={15}/></button></div>}
    {!error&&!loading&&tab==='saved'&&visible.length>0&&<div className="lifecycle-summary" aria-label="Saved job statuses">{lifecycleStatuses.map(status=><span key={status}><b>{visible.filter(j=>(applicationStatus[j.id]||'Saved')===status).length}</b> {status}</span>)}</div>}
    {!error&&!loading&&visible.map(job=><article className={'job-card '+(selected?.id===job.id?'job-card-active':'')} key={job.id}>
      <div className="job-logo"><Building2 size={25}/></div>
      <div className="job-main"><div className="job-topline"><span className="job-source">via {job.source_name}</span><span className="job-date"><Clock3 size={12}/> {dateText(job.date_posted)}</span></div>
        <button className="job-title" onClick={()=>setSelected(job)}>{job.job_title}</button>
        <div className="company-name">{job.company_name}</div>
        <div className="job-chips"><Chip><MapPin size={13}/>{job.location}</Chip><Chip>{job.country_code==='IN'?'India':'Germany'}</Chip><Chip>{job.work_mode}</Chip><Chip>{job.level}</Chip><Chip>{job.employment_type}</Chip><Chip>{experienceText(job)}</Chip><Chip>{languageText(job)}</Chip>
          {job.is_english_friendly&&<Chip accent><BadgeCheck size={13}/> English-friendly</Chip>}{job.visa_supported&&<Chip accent>Visa mentioned</Chip>}{job.salary_range&&<Chip>Salary disclosed</Chip>}</div>
        <div className="profile-tags">{job.persona_tags.map(id=><span key={id} className="profile-tag">{personaNames[id as PersonaId]||id}</span>)}</div>
        <div className="tag-row">{job.tech_stack.slice(0,6).map(s=><span key={s} className="tag">{s}</span>)}{job.tech_stack.length>6&&<span className="tag">+{job.tech_stack.length-6}</span>}</div>
      </div>
      <div className="job-actions"><Fit score={job.match_score} domain={job.domain_category} onClick={()=>setFitJob(job)}/><button aria-label={saved.includes(job.id)?'Remove saved job':'Save job'} className="bookmark" onClick={()=>toggleSaved(job.id,job)}>
        {saved.includes(job.id)?<BookmarkCheck size={21} className="text-accent"/>:<Bookmark size={21}/>}</button>
        {tab==='saved'&&<label className="application-label">Application status<select aria-label={'Application status for '+job.job_title} value={applicationStatus[job.id]||'Saved'} onChange={e=>setApplicationStatus(old=>({...old,[job.id]:e.target.value as ApplicationStatus}))}>{lifecycleStatuses.map(status=><option key={status} value={status}>{status}</option>)}</select></label>}
        <a href={job.official_apply_url} className="view-link" rel="noopener noreferrer" target="_blank">View role <ArrowUpRight size={15}/></a>
      </div>
    </article>)}
    {!error&&tab==='jobs'&&totalPages>1&&<div className="pagination"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>Previous</button>
      <span>Page {page} of {totalPages}</span><button disabled={page>=totalPages} onClick={()=>setPage(p=>p+1)}>Next</button></div>}
   </div>
   <aside className="side-pane"><div className="side-top"><div className="side-caption">YOUR SEARCH, SIMPLIFIED</div><h2>Better signals.<br/><span>Less noise.</span></h2>
     <p>Two job families, both countries, experience requirements clearly labeled, and technical relevance used for sorting.</p></div>
    <div className="side-divider"/>
    <div className="side-point"><span className="side-point-icon"><CodeXml size={18}/></span><div><strong>Engineering-first</strong><p>SQL, Python, Spark, Databricks and production pipelines.</p></div></div>
    <div className="side-point"><span className="side-point-icon"><ShieldCheck size={18}/></span><div><strong>Official links first</strong><p>Direct employer applications prioritized over aggregators.</p></div></div>
    <div className="side-point"><span className="side-point-icon"><Heart size={18}/></span><div><strong>Built around your fit</strong><p>Data engineering uses technical cosine similarity; SAP Basis uses a role-relevance score. Neither predicts hiring odds.</p></div></div>
    {result?.scanned_at&&<div className="side-timestamp">Last completed import: {new Date(result.scanned_at).toLocaleString('en-DE')}</div>}
   </aside></div>
  </main>
  <footer className="footer"><span>© 2026 DataPulse · Germany</span><span>Public employer job boards · No automatic applications</span></footer>
  {selected&&<div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setSelected(null)}}>
    <div className="detail" role="dialog" aria-modal="true" aria-label={selected.job_title}>
     <div className="detail-head"><span className="job-source">via {selected.source_name}</span><button className="detail-close" onClick={()=>setSelected(null)} aria-label="Close job details"><X size={21}/></button></div>
     <h2>{selected.job_title}</h2><p className="detail-company">{selected.company_name} · {selected.location}</p>
     <div className="detail-badges"><Fit score={selected.match_score} domain={selected.domain_category} onClick={()=>setFitJob(selected)}/><Chip>{selected.work_mode}</Chip><Chip>{selected.level}</Chip><Chip>{languageText(selected)}</Chip></div>
<h3>Profile eligibility</h3><p className="detail-description">{selected.persona_tags.map(id=>personaNames[id as PersonaId]||id).join(' · ')} · {experienceText(selected)}</p>
     {selected.salary_range&&<p className="detail-description"><strong>Salary disclosed:</strong> {selected.salary_range}</p>}
     {selected.visa_supported&&<p className="detail-description">Employer mentions visa sponsorship or work permit support; confirm terms before applying.</p>}
     {selected.relocation_assistance&&<p className="detail-description">Relocation assistance mentioned; this is not proof of visa sponsorship.</p>}
     <h3>Tech stack</h3><div className="tag-row">{selected.tech_stack.map(x=><span key={x} className="tag">{x}</span>)}</div>
     <h3>About this role</h3><p className="detail-description">{selected.description||'The employer has not provided a description in this feed. Read the complete job posting on the company website.'}</p>
     <div className="detail-foot"><button onClick={()=>toggleSaved(selected.id,selected)} className="save-large"><Bookmark size={17}/>{saved.includes(selected.id)?'Remove bookmark':'Save job'}</button>
       <a href={selected.official_apply_url} target="_blank" rel="noopener noreferrer" className="apply-large">Open official listing <ExternalLink size={17}/></a></div>
     <p className="detail-warning">Posting details can change. Confirm eligibility and availability directly with the employer.</p>
    </div>
   </div>}
  {fitJob&&<div className="modal-backdrop fit-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setFitJob(null)}}>
    <div className="detail fit-drawer" role="dialog" aria-modal="true" aria-label="Fit analysis">
      <div className="detail-head"><strong>Fit Analysis</strong><button className="detail-close" onClick={()=>setFitJob(null)} aria-label="Close fit analysis"><X size={21}/></button></div>
      <h2>{Math.round(fitJob.match_score*100)}% {fitJob.domain_category==='SAP Basis'?'role relevance':'technical relevance'}</h2>
      <p className="detail-company">{fitJob.job_title} · {fitJob.company_name}</p>
      <p className="fit-disclaimer">This is a profile-based skill comparison, not a prediction of hiring success or an analysis of your actual uploaded resume.</p>
      <h3><ClipboardCheck size={16}/> Matched skills</h3>
      <div className="tag-row">{fitDetails(fitJob).matched_skills.length>0?fitDetails(fitJob).matched_skills.map(s=><span key={s} className="tag fit-matched">{s}</span>):<span className="muted-copy">No preset skills directly matched.</span>}</div>
      <h3><ChartNoAxesCombined size={16}/> Missing / recommended skills</h3>
      <div className="tag-row">{fitDetails(fitJob).missing_or_recommended_skills.length>0?fitDetails(fitJob).missing_or_recommended_skills.map(s=><span key={s} className="tag">{s}</span>):<span className="muted-copy">No missing skills identified in this preset.</span>}</div>
      <h3>Experience fit</h3><p className="detail-description">{fitDetails(fitJob).experience_fit}. {experienceText(fitJob)}.</p>
      <p className="detail-warning">Recommended skills are suggestions; check the actual employer requirements before applying.</p>
    </div>
  </div>}
 </div>;
}
