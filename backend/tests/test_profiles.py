from dataclasses import replace
from datapulse_ingestion.models import RawJob
from datapulse_ingestion.profiles import (
    DE_JUNIOR,SAP_EXPERIENCED,SAP_STUDENT, parse_experience,country_of,normalize_for_profiles
)

SAP_TECH='SAP Basis, SAP NetWeaver, SAP HANA, Linux and S/4HANA.'
DE_TECH='Python SQL PySpark Databricks Apache Spark Azure Delta Lake ADLS Gen2'

def job(title='SAP Basis Consultant',location='Pune, India',desc='Minimum 5 years of experience. '+SAP_TECH,**kw):
    return RawJob(source_platform='Personio',source_id='test:42',company_name='Test GmbH',
                  job_title=title,location=location,job_url='https://jobs.example.com/42',
                  description=desc,**kw)


def test_experience_parses_range_and_5_plus():
    a=parse_experience('You must have 3–6 years of experience with SAP Basis')
    assert (a.minimum,a.maximum)==(3,6)
    b=parse_experience('Minimum 5 years of experience with SAP Basis')
    assert (b.minimum,b.maximum)==(5,None)
    c=parse_experience('7+ years of experience in data engineering')
    assert c.minimum==7
    d=parse_experience('Our company has 20 years of industry history')
    assert d.minimum is None


def test_sap_experienced_5_years_india():
    j,why=normalize_for_profiles(job())
    assert why=='accepted'
    assert j.country_code=='IN' and j.persona_tags==[SAP_EXPERIENCED]
    assert j.min_experience_years==5 and j.max_experience_years is None
    assert j.job_level=='Mid' and j.domain_category=='SAP Basis'


def test_sap_experienced_germany_and_remote_india():
    for location,code in [('Berlin, Germany','DE'),('Remote - India','IN')]:
        j,why=normalize_for_profiles(job(location=location,desc='3-6 years of experience in SAP Basis. '+SAP_TECH))
        assert why=='accepted' and j.country_code==code and j.max_experience_years==6


def test_sap_experienced_accepts_0_to_5_or_unknown_but_rejects_over_five():
    for desc in ['2 years of experience. '+SAP_TECH,'SAP Basis and SAP NetWeaver']:
        j,_=normalize_for_profiles(job(desc=desc))
        assert j is not None and j.persona_tags==[SAP_EXPERIENCED]
    j,_=normalize_for_profiles(job(desc='9+ years of experience. '+SAP_TECH))
    assert j is None
    j,_=normalize_for_profiles(job(title='Head of SAP Basis',desc='5 years of experience. '+SAP_TECH))
    assert j is None


def test_sap_student_entry_roles_share_five_years_cap():
    for title in ['SAP Basis Werkstudent','SAP Basis Intern','SAP Basis Trainee','SAP Basis Junior Consultant']:
        j,why=normalize_for_profiles(job(title=title,location='Hamburg, Germany',desc='Up to 2 years of SAP Basis experience'))
        assert why=='accepted' and j.persona_tags==[SAP_STUDENT]
        assert j.country_code=='DE'
        rejected,_=normalize_for_profiles(job(title=title,location='Germany',desc='10+ years of experience with SAP Basis'))
        assert rejected is None


def test_sap_intern_is_included_in_india():
    j,_=normalize_for_profiles(job(title='SAP Basis Intern',location='Mumbai, India'))
    assert j is not None and j.persona_tags==[SAP_STUDENT] and j.country_code=='IN'


def test_data_junior_requires_germany_max_two_years():
    j,why=normalize_for_profiles(job(title='Data Engineer',location='Remote Germany',desc='2 years of experience. Excellent English. '+DE_TECH))
    assert why=='accepted' and j.persona_tags==[DE_JUNIOR]
    assert j.country_code=='DE' and j.min_experience_years==2
    assert j.technical_match_score>=.70
    for desc in ['3+ years of experience. Excellent English. '+DE_TECH,
                 '5 years of experience. Excellent English. '+DE_TECH]:
        result,_=normalize_for_profiles(job(title='Data Engineer',location='Germany',desc=desc))
        assert result is None


def test_data_engineer_with_unknown_experience_is_included_without_inventing_zero():
    desc='Excellent English; Python SQL PySpark Databricks Azure Spark'
    a,_=normalize_for_profiles(job(title='Data Engineer',location='Germany',desc=desc))
    b,_=normalize_for_profiles(job(title='Junior Data Engineer',location='Germany',desc=desc))
    assert a is not None and b is not None
    assert a.min_experience_years is None and b.min_experience_years is None


def test_data_junior_in_india_but_not_ambiguous_global_remote():
    desc='2 years of experience. Excellent English. '+DE_TECH
    india,_=normalize_for_profiles(job(title='Junior Data Engineer',location='Bengaluru, India',desc=desc))
    ambiguous,_=normalize_for_profiles(job(title='Junior Data Engineer',location='Remote worldwide',desc=desc))
    assert india is not None and india.country_code=='IN'
    assert ambiguous is None


def test_low_technical_match_and_german_mandatory_never_exclude():
    for country in ('Berlin, Germany','Bengaluru, India'):
        j,why=normalize_for_profiles(job(title='Data Engineer',location=country,
            desc='Gute Deutschkenntnisse sind erforderlich. Berufseinsteiger willkommen.'))
        assert why=='accepted' and j.technical_match_score<.70
        assert j.min_experience_years is None
        assert 'German' in j.language_required


def test_full_time_and_internship_are_eligible():
    for title,contract in [('Data Engineer','Full-Time'),('Data Engineering Intern','Internship'),
                           ('Data Engineering Working Student','Working Student')]:
        j,why=normalize_for_profiles(job(title=title,location='Mumbai, India',
            desc='Python and SQL',employment_type=contract))
        assert why=='accepted', title
        if title=='Data Engineer':
            assert j.job_level=='Mid' and j.employment_type=='Full-time'


def test_country_not_inferred_from_ambiguous_remote():
    assert country_of(job(location='Remote worldwide')) is None
    assert country_of(job(location='Germany and India')) is None
    assert country_of(job(location='remote',country_code='US')) is None


def test_sap_does_not_require_de_skill_threshold():
    j,_=normalize_for_profiles(job(title='SAP Basis Werkstudent',location='Germany',desc='Learn the SAP Basis environment.'))
    assert j is not None and j.persona_tags==[SAP_STUDENT]


def test_sap_experienced_does_not_claim_english_friendliness():
    j,_=normalize_for_profiles(job(desc='Minimum 5 years of experience. Very good German and English skills required. '+SAP_TECH))
    assert j is not None and not j.is_english_friendly


def test_no_prohibited_director_title():
    assert normalize_for_profiles(job(title='Director SAP Basis',location='India'))[0] is None


def test_accepts_minimum_5_years_hana_admin():
    j,_=normalize_for_profiles(job(title='SAP HANA Administration Consultant',location='Germany'))
    assert j and j.persona_tags==[SAP_EXPERIENCED]
