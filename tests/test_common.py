from src.duckdb_compare import DUCKDB_ANALYST_ROLES
from src.common import ANALYST_ROLES, is_analyst, parse_skills, salary_estimate

def test_skills_are_normalized_deduplicated_and_sorted():
    assert parse_skills(" SQL ; sql;  Python   3 ; ;BPMN ") == ["bpmn", "python 3", "sql"]


def test_variant_filter_uses_explicit_role_list():
    assert is_analyst({"role": "Системный аналитик"})
    assert is_analyst({"role": "Аналитик"})
    assert not is_analyst({"role": "Программист, разработчик"})
    assert set(ANALYST_ROLES) == {
        "Аналитик", "Системный аналитик", "Бизнес-аналитик",
        "BI-аналитик, аналитик данных", "Продуктовый аналитик",
        "Руководитель отдела аналитики",
    }

def test_salary_estimate_uses_range_midpoint_and_rur_only():
    assert salary_estimate({"currency": "RUR", "salary_from": "100000", "salary_to": "140000"}) == 120000
    assert salary_estimate({"currency": "RUR", "salary_from": "100000", "salary_to": ""}) == 100000
    assert salary_estimate({"currency": "USD", "salary_from": "100000", "salary_to": ""}) is None
    assert salary_estimate({"currency": "RUR", "salary_from": "1000", "salary_to": ""}) is None

def test_duckdb_filter_matches_mapreduce_variant():
    assert set(DUCKDB_ANALYST_ROLES) == set(ANALYST_ROLES)