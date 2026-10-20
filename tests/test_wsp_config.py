"""WSP configuration: Dash BPO uses MICT SETA only, with a fixed SDL reference."""
from tests.conftest import query


def test_only_mict_seta_offered(app_module):
    assert app_module.SETAS == ["MICT SETA"]


def test_company_profile_has_mict_seta_and_sdl(app_module):
    rows = query(app_module, "SELECT seta, sdl_number FROM company_profiles ORDER BY id DESC LIMIT 1")
    assert rows, "a company profile version should be seeded by the migration"
    assert rows[0][0] == "MICT SETA"
    assert rows[0][1] == "L640813558"
