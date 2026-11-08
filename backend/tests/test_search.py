import pytest
from app.search import boolean_condition,SearchSyntaxError

def test_boolean_and_phrases():
 params=[];sql=boolean_condition('"Data Engineer" AND PySpark',params)
 assert 'AND' in sql and '$1' in sql and '$2' in sql
 assert params==['%data engineer%','%pyspark%']

def test_or_and_not():
 params=[];sql=boolean_condition('(Databricks OR Spark) AND NOT Director',params)
 assert 'NOT' in sql and 'OR' in sql and len(params)==3

@pytest.mark.parametrize('query',['A OR','AND Python','(Python OR SQL','a'*501])
def test_bad_boolean(query):
 with pytest.raises(SearchSyntaxError):boolean_condition(query,[])
