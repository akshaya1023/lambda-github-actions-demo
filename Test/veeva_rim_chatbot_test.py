import json
import pytest
from unittest.mock import patch, MagicMock

import lambda_function

# -----------------------------
# Authentication Tests
# -----------------------------


@patch("lambda_function.urllib.request.urlopen")
def test_authenticate_success(mock_urlopen):
    response = MagicMock()
    response.read.return_value = json.dumps({"sessionId": "ABC123"}).encode()

    mock_urlopen.return_value.__enter__.return_value = response

    result = lambda_function._authenticate()

    assert result == "ABC123"


@patch("lambda_function.urllib.request.urlopen")
def test_authenticate_no_session(mock_urlopen):
    response = MagicMock()
    response.read.return_value = json.dumps({"responseStatus": "FAILURE"}).encode()

    mock_urlopen.return_value.__enter__.return_value = response

    with pytest.raises(RuntimeError):
        lambda_function._authenticate()


@patch("lambda_function._authenticate")
def test_get_session_first_time(mock_auth):
    lambda_function._session_id = None

    mock_auth.return_value = "SESSION123"

    session = lambda_function.get_session_id()

    assert session == "SESSION123"


def test_get_session_cached():
    lambda_function._session_id = "CACHE123"

    session = lambda_function.get_session_id()

    assert session == "CACHE123"


def test_invalidate_session():
    lambda_function._session_id = "ABC"

    lambda_function._invalidate_session()

    assert lambda_function._session_id is None


# -----------------------------
# HTTP GET
# -----------------------------


@patch("lambda_function.urllib.request.urlopen")
def test_http_get_success(mock_urlopen):

    response = MagicMock()
    response.read.return_value = json.dumps(
        {"responseStatus": "SUCCESS", "data": []}
    ).encode()

    mock_urlopen.return_value.__enter__.return_value = response

    result = lambda_function._http_get("http://example.com", {})

    assert result["responseStatus"] == "SUCCESS"


@patch("lambda_function.urllib.request.urlopen")
def test_http_get_http_error(mock_urlopen):

    error_response = MagicMock()
    error_response.read.return_value = json.dumps(
        {"responseStatus": "FAILURE"}
    ).encode()

    from urllib.error import HTTPError

    error = HTTPError(
        url="http://example.com",
        code=400,
        msg="Bad Request",
        hdrs=None,
        fp=error_response,
    )

    mock_urlopen.side_effect = error

    result = lambda_function._http_get("http://example.com", {})

    assert result["responseStatus"] == "FAILURE"


# -----------------------------
# Success Helper
# -----------------------------


def test_is_success_success():
    assert lambda_function._is_success({"responseStatus": "SUCCESS"})


def test_is_success_warning():
    assert lambda_function._is_success({"responseStatus": "WARNING"})


def test_is_success_failure():
    assert not lambda_function._is_success({"responseStatus": "FAILURE"})


# -----------------------------
# Vault Query Retry
# -----------------------------


@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_query_success(mock_session, mock_http):

    mock_session.return_value = "SESSION"

    mock_http.return_value = {"responseStatus": "SUCCESS", "data": []}

    result = lambda_function._vault_query("SELECT id FROM application__v")

    assert result["responseStatus"] == "SUCCESS"


@patch("lambda_function._invalidate_session")
@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_query_retry(mock_session, mock_http, mock_invalidate):

    mock_session.return_value = "SESSION"

    mock_http.side_effect = [
        {"responseStatus": "FAILURE", "errors": [{"type": "INVALID_SESSION_ID"}]},
        {"responseStatus": "SUCCESS", "data": []},
    ]

    result = lambda_function._vault_query("SELECT id FROM application__v")

    assert result["responseStatus"] == "SUCCESS"

    mock_invalidate.assert_called_once()


# --------------------------------------------------------
# _vault_next_page
# --------------------------------------------------------


@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_next_page_success(mock_session, mock_http):

    mock_session.return_value = "SESSION"

    mock_http.return_value = {"responseStatus": "SUCCESS", "data": []}

    result = lambda_function._vault_next_page("/next")

    assert result["responseStatus"] == "SUCCESS"


@patch("lambda_function._invalidate_session")
@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_next_page_retry(mock_session, mock_http, mock_invalidate):

    mock_session.return_value = "SESSION"

    mock_http.side_effect = [
        {"responseStatus": "FAILURE", "errors": [{"type": "INVALID_SESSION_ID"}]},
        {"responseStatus": "SUCCESS", "data": []},
    ]

    result = lambda_function._vault_next_page("/next")

    assert result["responseStatus"] == "SUCCESS"

    mock_invalidate.assert_called_once()


# --------------------------------------------------------
# _vault_get
# --------------------------------------------------------


@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_get_success(mock_session, mock_http):

    mock_session.return_value = "SESSION"

    mock_http.return_value = {"responseStatus": "SUCCESS", "data": []}

    result = lambda_function._vault_get("/objects")

    assert result["responseStatus"] == "SUCCESS"


@patch("lambda_function._invalidate_session")
@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_get_retry(mock_session, mock_http, mock_invalidate):

    mock_session.return_value = "SESSION"

    mock_http.side_effect = [
        {"responseStatus": "FAILURE", "errors": [{"type": "INVALID_SESSION_ID"}]},
        {"responseStatus": "SUCCESS", "data": []},
    ]

    result = lambda_function._vault_get("/objects")

    assert result["responseStatus"] == "SUCCESS"

    mock_invalidate.assert_called_once()


# --------------------------------------------------------
# _all_pages
# --------------------------------------------------------


@patch("lambda_function._vault_next_page")
def test_all_pages(mock_next):

    first = {"data": [{"id": "1"}], "responseDetails": {"next_page": "/page2"}}

    mock_next.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"id": "2"}],
        "responseDetails": {"next_page": ""},
    }

    result = lambda_function._all_pages(first)

    assert len(result) == 2


@patch("lambda_function._vault_next_page")
def test_all_pages_no_next(mock_next):

    first = {"data": [{"id": "1"}], "responseDetails": {"next_page": ""}}

    result = lambda_function._all_pages(first)

    assert len(result) == 1

    mock_next.assert_not_called()


# --------------------------------------------------------
# Country Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_country_cache(mock_query, mock_pages):

    lambda_function._country_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [
        {"id": "1", "name__v": "India"},
        {"id": "2", "name__v": "Japan"},
    ]

    result = lambda_function._get_country_id_map()

    assert result["1"] == "India"

    assert result["2"] == "Japan"


def test_country_cache_existing():

    lambda_function._country_id_map = {"1": "India"}

    result = lambda_function._get_country_id_map()

    assert result["1"] == "India"


# --------------------------------------------------------
# Application Type Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_app_type_cache(mock_query, mock_pages):

    lambda_function._app_type_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"id": "10", "name__v": "NDA"}]

    result = lambda_function._get_app_type_id_map()

    assert result["10"] == "NDA"


# --------------------------------------------------------
# Dossier Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_dossier_cache(mock_query, mock_pages):

    lambda_function._dossier_format_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"id": "20", "name__v": "eCTD"}]

    result = lambda_function._get_dossier_format_id_map()

    assert result["20"] == "eCTD"


# --------------------------------------------------------
# Region Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_region_cache(mock_query, mock_pages):

    lambda_function._region_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"id": "30", "name__v": "Asia Pacific"}]

    result = lambda_function._get_region_id_map()

    assert result["30"] == "Asia Pacific"


# --------------------------------------------------------
# Health Authority Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_get_ha_id_map(mock_query, mock_pages):

    lambda_function._ha_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"id": "HA1", "name__v": "FDA"}]

    result = lambda_function._get_ha_id_map()

    assert result["HA1"] == "FDA"


# --------------------------------------------------------
# Health Authority Center Cache
# --------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_get_ha_center_map(mock_query, mock_pages):

    lambda_function._ha_center_id_map = None

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"id": "C1", "name__v": "CDER"}]

    result = lambda_function._get_ha_center_id_map()

    assert result["C1"] == "CDER"


# --------------------------------------------------------
# Resolve Helpers
# --------------------------------------------------------


def test_resolve_country():

    lambda_function._country_id_map = {"1": "India"}

    assert lambda_function._resolve_country("1") == "India"


def test_resolve_type():

    lambda_function._app_type_id_map = {"A": "NDA"}

    assert lambda_function._resolve_type("A") == "NDA"


def test_resolve_format():

    lambda_function._dossier_format_id_map = {"F": "eCTD"}

    assert lambda_function._resolve_format("F") == "eCTD"


def test_resolve_region():

    lambda_function._region_id_map = {"R": "Asia Pacific"}

    assert lambda_function._resolve_region("R") == "Asia Pacific"


def test_resolve_ha():

    lambda_function._ha_id_map = {"H": "FDA"}

    assert lambda_function._resolve_ha("H") == "FDA"


# --------------------------------------------------------
# Normalize
# --------------------------------------------------------


def test_normalize_space():

    assert lambda_function._normalize("New Zealand") == "newzealand"


def test_normalize_dash():

    assert lambda_function._normalize("United-States") == "unitedstates"


def test_normalize_underscore():

    assert lambda_function._normalize("South_Africa") == "southafrica"


# --------------------------------------------------------
# Find Country
# --------------------------------------------------------


def test_find_country_exact():

    lambda_function._country_id_map = {"1": "India"}

    assert lambda_function._find_country_id("India") == "1"


def test_find_country_case():

    lambda_function._country_id_map = {"3": "Japan"}

    assert lambda_function._find_country_id("japan") == "3"


def test_find_country_not_found():

    lambda_function._country_id_map = {"1": "India"}

    assert lambda_function._find_country_id("Mars") is None


# --------------------------------------------------------
# Market Abbreviation
# --------------------------------------------------------


def test_market_abbrev_builtin():

    assert lambda_function._market_abbrev("United States") == "USA"


def test_market_abbrev_default():

    assert lambda_function._market_abbrev("India") == "IND"


# --------------------------------------------------------
# No Vault Messages
# --------------------------------------------------------


def test_no_vault_data():

    msg = lambda_function._no_vault_data("India", "application type")

    assert "India" in msg

    assert "application type" in msg


def test_country_not_in_vault():

    msg = lambda_function._country_not_in_vault("Atlantis")

    assert "Atlantis" in msg


# --------------------------------------------------------
# Application Type Lookup
# --------------------------------------------------------


def test_find_application_type_exact():

    lambda_function._app_type_id_map = {"1": "New Drug Application"}

    result = lambda_function._find_application_type("New Drug Application")

    assert result == "New Drug Application"


def test_find_application_type_partial():

    lambda_function._app_type_id_map = {"1": "New Drug Application (NDA)"}

    result = lambda_function._find_application_type("nda")

    assert result == "New Drug Application (NDA)"


# --------------------------------------------------------
# Resolve Health Authority ID
# --------------------------------------------------------


def test_resolve_ha_id():

    lambda_function._ha_id_map = {"10": "FDA"}

    hid, name = lambda_function._resolve_ha_id("FDA")

    assert hid == "10"

    assert name == "FDA"


# --------------------------------------------------------
# Name Exists
# --------------------------------------------------------


@patch("lambda_function._vault_query")
def test_name_exists_true(mock_query):

    mock_query.return_value = {"responseStatus": "SUCCESS", "data": [{"id": "100"}]}

    assert lambda_function._name_exists("APP001")


@patch("lambda_function._vault_query")
def test_name_exists_false(mock_query):

    mock_query.return_value = {"responseStatus": "SUCCESS", "data": []}

    assert not lambda_function._name_exists("APP001")


# ==========================================================
# Business Function Tests - Part 2A
# ==========================================================


# ----------------------------------------------------------
# _fn_get_lead_markets
# ----------------------------------------------------------


@patch("lambda_function._get_country_id_map")
@patch("lambda_function._vault_query")
def test_get_lead_markets(mock_query, mock_country):

    mock_country.return_value = {"1": "India", "2": "Japan"}

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [
            {"lead_market__rim": "1"},
            {"lead_market__rim": "2"},
            {"lead_market__rim": "1"},
        ],
    }

    result = lambda_function._fn_get_lead_markets()

    assert "India" in result
    assert "Japan" in result


@patch("lambda_function._vault_query")
def test_get_lead_markets_empty(mock_query):

    mock_query.return_value = {"responseStatus": "SUCCESS", "data": []}

    result = lambda_function._fn_get_lead_markets()

    assert "No Lead Markets" in result


@patch("lambda_function._vault_query")
def test_get_lead_markets_failure(mock_query):

    mock_query.return_value = {"responseStatus": "FAILURE"}

    result = lambda_function._fn_get_lead_markets()

    assert "Unable" in result


# ----------------------------------------------------------
# _fn_get_product_families
# ----------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
def test_get_product_families(mock_query, mock_pages):

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"name__v": "Metformin"}, {"name__v": "Adalimumab"}]

    result = lambda_function._fn_get_product_families()

    assert "Metformin" in result
    assert "Adalimumab" in result


@patch("lambda_function._vault_query")
def test_get_product_families_none(mock_query):

    mock_query.return_value = {"responseStatus": "FAILURE"}

    result = lambda_function._fn_get_product_families()

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_get_dossier_formats
# ----------------------------------------------------------


@patch("lambda_function._get_dossier_format_id_map")
def test_get_dossier_formats(mock_formats):

    mock_formats.return_value = {"1": "eCTD", "2": "NeeS"}

    result = lambda_function._fn_get_dossier_formats()

    assert "eCTD" in result
    assert "NeeS" in result


@patch("lambda_function._get_dossier_format_id_map")
def test_get_dossier_formats_empty(mock_formats):

    mock_formats.return_value = {}

    result = lambda_function._fn_get_dossier_formats()

    assert "No Dossier Format" in result


# ----------------------------------------------------------
# _fn_get_dossier_format_for_market
# ----------------------------------------------------------


@patch("lambda_function._resolve_format")
@patch("lambda_function._vault_query")
@patch("lambda_function._find_country_id")
def test_get_dossier_market(mock_country, mock_query, mock_format):

    mock_country.return_value = "100"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"dossier_format__v": "1"}],
    }

    mock_format.return_value = "eCTD"

    result = lambda_function._fn_get_dossier_format_for_market("India")

    assert "eCTD" in result


@patch("lambda_function._find_country_id")
def test_get_dossier_market_country_missing(mock_country):

    mock_country.return_value = None

    result = lambda_function._fn_get_dossier_format_for_market("Moon")

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_get_application_types_for_market
# ----------------------------------------------------------


@patch("lambda_function._resolve_type")
@patch("lambda_function._vault_query")
@patch("lambda_function._find_country_id")
def test_get_application_types(mock_country, mock_query, mock_type):

    mock_country.return_value = "10"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"application_type__rim": "100"}],
    }

    mock_type.return_value = "NDA"

    result = lambda_function._fn_get_application_types_for_market("India")

    assert "NDA" in result


@patch("lambda_function._find_country_id")
def test_get_application_types_country_missing(mock_country):

    mock_country.return_value = None

    result = lambda_function._fn_get_application_types_for_market("Mars")

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_search_application_type
# ----------------------------------------------------------


@patch("lambda_function._get_app_type_id_map")
def test_search_application_type(mock_map):

    mock_map.return_value = {"1": "New Drug Application (NDA)", "2": "ANDA"}

    result = lambda_function._fn_search_application_type("nda")

    assert "NDA" in result


@patch("lambda_function._get_app_type_id_map")
def test_search_application_type_none(mock_map):

    mock_map.return_value = {}

    result = lambda_function._fn_search_application_type("xyz")

    assert "No Application Types" in result


# ==========================================================
# Business Function Tests - Part 2B
# ==========================================================


# ----------------------------------------------------------
# _fn_validate_application_type_for_market
# ----------------------------------------------------------


@patch("lambda_function._find_application_type")
@patch("lambda_function._find_country_id")
@patch("lambda_function._vault_query")
@patch("lambda_function._resolve_type")
def test_validate_application_type_used(
    mock_resolve, mock_query, mock_country, mock_find
):

    mock_find.return_value = "NDA"
    mock_country.return_value = "100"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"application_type__rim": "1"}],
    }

    mock_resolve.return_value = "NDA"

    result = lambda_function._fn_validate_application_type_for_market("NDA", "India")

    assert "Yes" in result


@patch("lambda_function._find_application_type")
def test_validate_application_type_invalid(mock_find):

    mock_find.return_value = None

    result = lambda_function._fn_validate_application_type_for_market("XYZ", "India")

    assert "not a recognised" in result


@patch("lambda_function._find_application_type")
@patch("lambda_function._find_country_id")
def test_validate_application_country_missing(mock_country, mock_find):

    mock_find.return_value = "NDA"
    mock_country.return_value = None

    result = lambda_function._fn_validate_application_type_for_market("NDA", "India")

    assert "general regulatory knowledge" in result


# ----------------------------------------------------------
# _fn_get_regions
# ----------------------------------------------------------


@patch("lambda_function._get_region_id_map")
def test_get_regions(mock_region):

    mock_region.return_value = {"1": "Asia", "2": "Europe"}

    result = lambda_function._fn_get_regions()

    assert "Asia" in result
    assert "Europe" in result


@patch("lambda_function._get_region_id_map")
def test_get_regions_empty(mock_region):

    mock_region.return_value = {}

    result = lambda_function._fn_get_regions()

    assert "No regions" in result


# ----------------------------------------------------------
# _fn_get_region_for_market
# ----------------------------------------------------------


@patch("lambda_function._resolve_region")
@patch("lambda_function._vault_query")
@patch("lambda_function._find_country_id")
def test_get_region_market(mock_country, mock_query, mock_region):

    mock_country.return_value = "10"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"region__v": "5"}],
    }

    mock_region.return_value = "Asia"

    result = lambda_function._fn_get_region_for_market("India")

    assert "Asia" in result


@patch("lambda_function._find_country_id")
def test_get_region_market_missing(mock_country):

    mock_country.return_value = None

    result = lambda_function._fn_get_region_for_market("Moon")

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_get_countries_for_region
# ----------------------------------------------------------


@patch("lambda_function._all_pages")
@patch("lambda_function._vault_query")
@patch("lambda_function._get_region_id_map")
def test_get_countries_for_region(mock_region, mock_query, mock_pages):

    mock_region.return_value = {"1": "Asia"}

    mock_query.return_value = {"responseStatus": "SUCCESS"}

    mock_pages.return_value = [{"name__v": "India"}, {"name__v": "Japan"}]

    result = lambda_function._fn_get_countries_for_region("Asia")

    assert "India" in result
    assert "Japan" in result


@patch("lambda_function._get_region_id_map")
def test_get_countries_unknown_region(mock_region):

    mock_region.return_value = {"1": "Asia"}

    result = lambda_function._fn_get_countries_for_region("Africa")

    assert "not found" in result


# ----------------------------------------------------------
# _fn_get_all_health_authorities
# ----------------------------------------------------------


@patch("lambda_function._get_ha_id_map")
def test_get_all_health_authorities(mock_map):

    mock_map.return_value = {"1": "FDA", "2": "EMA"}

    result = lambda_function._fn_get_all_health_authorities()

    assert "FDA" in result
    assert "EMA" in result


@patch("lambda_function._get_ha_id_map")
def test_get_all_health_authorities_empty(mock_map):

    mock_map.return_value = {}

    result = lambda_function._fn_get_all_health_authorities()

    assert "No Health Authorities" in result


# ----------------------------------------------------------
# _fn_get_health_authority_for_market
# ----------------------------------------------------------


@patch("lambda_function._resolve_ha")
@patch("lambda_function._vault_query")
@patch("lambda_function._find_country_id")
def test_get_health_authority_market(mock_country, mock_query, mock_resolve):

    mock_country.return_value = "100"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [{"health_authority__v": "10"}],
    }

    mock_resolve.return_value = "FDA"

    result = lambda_function._fn_get_health_authority_for_market("India")

    assert "FDA" in result


@patch("lambda_function._find_country_id")
def test_get_health_authority_market_missing(mock_country):

    mock_country.return_value = None

    result = lambda_function._fn_get_health_authority_for_market("Mars")

    assert "VAULT_NO_RECORDS" in result


# ==========================================================
# Business Function Tests - Part 2C
# ==========================================================


# ----------------------------------------------------------
# _fn_get_health_authority_centers
# ----------------------------------------------------------


@patch("lambda_function._get_ha_center_id_map")
@patch("lambda_function._vault_query")
@patch("lambda_function._resolve_ha_id")
def test_get_health_authority_centers(mock_resolve, mock_query, mock_center):

    mock_resolve.return_value = ("10", "FDA")

    mock_center.return_value = {"1": "CDER", "2": "CBER"}

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [
            {"health_authority_center__v": "1"},
            {"health_authority_center__v": "2"},
        ],
    }

    result = lambda_function._fn_get_health_authority_centers("FDA")

    assert "CDER" in result
    assert "CBER" in result


@patch("lambda_function._resolve_ha_id")
@patch("lambda_function._get_ha_center_id_map")
def test_get_health_authority_centers_all(mock_center, mock_resolve):

    mock_resolve.return_value = (None, None)

    mock_center.return_value = {"1": "CDER", "2": "CBER"}

    result = lambda_function._fn_get_health_authority_centers("FDA")

    assert "CDER" in result


@patch("lambda_function._resolve_ha_id")
@patch("lambda_function._get_ha_center_id_map")
def test_get_health_authority_centers_none(mock_center, mock_resolve):

    mock_resolve.return_value = (None, None)

    mock_center.return_value = {}

    result = lambda_function._fn_get_health_authority_centers("FDA")

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_suggest_application_name
# ----------------------------------------------------------


@patch("lambda_function._name_exists")
def test_suggest_application_name(mock_exists):

    mock_exists.return_value = False

    result = lambda_function._fn_suggest_application_name(
        "Metformin", "New Drug Application (NDA)", "India"
    )

    assert "Suggested Application Name" in result


@patch("lambda_function._name_exists")
def test_suggest_application_duplicate(mock_exists):

    mock_exists.side_effect = [True, False]

    result = lambda_function._fn_suggest_application_name(
        "Metformin", "New Drug Application (NDA)", "India"
    )

    assert "_V2" in result


# ----------------------------------------------------------
# _fn_check_application_name
# ----------------------------------------------------------


@patch("lambda_function._name_exists")
def test_check_application_exists(mock_exists):

    mock_exists.return_value = True

    result = lambda_function._fn_check_application_name("APP001")

    assert "already exists" in result


@patch("lambda_function._name_exists")
def test_check_application_not_exists(mock_exists):

    mock_exists.return_value = False

    result = lambda_function._fn_check_application_name("APP001")

    assert "safe to use" in result


# ----------------------------------------------------------
# _fn_get_existing_applications
# ----------------------------------------------------------


@patch("lambda_function._resolve_ha")
@patch("lambda_function._resolve_region")
@patch("lambda_function._resolve_format")
@patch("lambda_function._resolve_type")
@patch("lambda_function._vault_query")
@patch("lambda_function._find_country_id")
def test_get_existing_applications(
    mock_country, mock_query, mock_type, mock_format, mock_region, mock_ha
):

    mock_country.return_value = "100"

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [
            {
                "name__v": "APP1",
                "application_type__rim": "1",
                "dossier_format__v": "2",
                "region__v": "3",
                "health_authority__v": "4",
            }
        ],
    }

    mock_type.return_value = "NDA"
    mock_format.return_value = "eCTD"
    mock_region.return_value = "Asia"
    mock_ha.return_value = "FDA"

    result = lambda_function._fn_get_existing_applications("India")

    assert "APP1" in result
    assert "FDA" in result


@patch("lambda_function._find_country_id")
def test_get_existing_applications_country_missing(mock_country):

    mock_country.return_value = None

    result = lambda_function._fn_get_existing_applications("Moon")

    assert "VAULT_NO_RECORDS" in result


# ----------------------------------------------------------
# _fn_get_recent_applications
# ----------------------------------------------------------


@patch("lambda_function._get_country_id_map")
@patch("lambda_function._resolve_format")
@patch("lambda_function._resolve_type")
@patch("lambda_function._vault_query")
def test_get_recent_applications(mock_query, mock_type, mock_format, mock_country):

    mock_country.return_value = {"100": "India"}

    mock_query.return_value = {
        "responseStatus": "SUCCESS",
        "data": [
            {
                "name__v": "APP1",
                "lead_market__rim": "100",
                "application_type__rim": "1",
                "dossier_format__v": "2",
            }
        ],
    }

    mock_type.return_value = "NDA"
    mock_format.return_value = "eCTD"

    result = lambda_function._fn_get_recent_applications()

    assert "APP1" in result
    assert "India" in result


@patch("lambda_function._vault_query")
def test_get_recent_applications_empty(mock_query):

    mock_query.return_value = {"responseStatus": "SUCCESS", "data": []}

    result = lambda_function._fn_get_recent_applications()

    assert "No existing applications" in result


# ==========================================================
# Handler and Dispatch Tests - Part 3
# ==========================================================


# ----------------------------------------------------------
# Warmup Event
# ----------------------------------------------------------


def test_lambda_handler_warmup():

    event = {"warmup": True}

    result = lambda_function.lambda_handler(event, None)

    assert result["statusCode"] == 200


# ----------------------------------------------------------
# Unknown Function
# ----------------------------------------------------------


def test_lambda_handler_unknown_function():

    event = {"actionGroup": "VaultQueries", "function": "dummy", "parameters": []}

    result = lambda_function.lambda_handler(event, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert "Unknown function" in body


# ----------------------------------------------------------
# queryVault Success
# ----------------------------------------------------------


@patch("lambda_function._dispatch")
def test_lambda_handler_queryvault(mock_dispatch):

    mock_dispatch.return_value = "Success"

    event = {
        "actionGroup": "VaultQueries",
        "function": "queryVault",
        "parameters": [
            {"name": "queryType", "value": "getLeadMarkets"},
            {"name": "parameters", "value": "{}"},
        ],
    }

    result = lambda_function.lambda_handler(event, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert body == "Success"


# ----------------------------------------------------------
# queryVault Missing Query Type
# ----------------------------------------------------------


def test_lambda_handler_missing_querytype():

    event = {"actionGroup": "VaultQueries", "function": "queryVault", "parameters": []}

    result = lambda_function.lambda_handler(event, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert "queryType" in body


# ----------------------------------------------------------
# Exception Handling
# ----------------------------------------------------------


@patch("lambda_function._dispatch")
def test_lambda_handler_exception(mock_dispatch):

    mock_dispatch.side_effect = Exception("Boom")

    event = {
        "actionGroup": "VaultQueries",
        "function": "queryVault",
        "parameters": [
            {"name": "queryType", "value": "getLeadMarkets"},
            {"name": "parameters", "value": "{}"},
        ],
    }

    result = lambda_function.lambda_handler(event, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert "error retrieving data" in body.lower()


# ==========================================================
# Dispatch Tests
# ==========================================================


@patch("lambda_function._fn_get_lead_markets")
def test_dispatch_lead_markets(mock_fn):

    mock_fn.return_value = "OK"

    def p(name):
        return ""

    result = lambda_function._dispatch("getLeadMarkets", p)

    assert result == "OK"


@patch("lambda_function._fn_get_product_families")
def test_dispatch_product_families(mock_fn):

    mock_fn.return_value = "OK"

    def p(name):
        return ""

    result = lambda_function._dispatch("getProductFamilies", p)

    assert result == "OK"


def test_dispatch_unknown():

    def p(name):
        return ""

    result = lambda_function._dispatch("INVALID", p)

    assert "Unknown queryType" in result


# ----------------------------------------------------------
# Parameter function
# ----------------------------------------------------------


def test_dispatch_parameter_function():

    values = {"leadMarket": "India"}

    def p(name):
        return values[name]

    assert p("leadMarket") == "India"


def test_find_country_partial_match():

    lambda_function._country_id_map = {"2": "United States of America"}

    assert lambda_function._find_country_id("United States") == "2"


def test_normalize_empty():

    assert lambda_function._normalize("") == ""


@patch("lambda_function.urllib.request.urlopen")
def test_authenticate_exception(mock_urlopen):

    mock_urlopen.side_effect = Exception("Connection Error")

    with pytest.raises(Exception):
        lambda_function._authenticate()


def test_is_success_missing_status():

    assert not lambda_function._is_success({})


@patch("lambda_function._invalidate_session")
@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_query_retry_failed(mock_session, mock_http, mock_invalidate):

    mock_session.return_value = "SESSION"

    mock_http.side_effect = [
        {"responseStatus": "FAILURE", "errors": [{"type": "INVALID_SESSION_ID"}]},
        {"responseStatus": "FAILURE"},
    ]

    result = lambda_function._vault_query("SELECT id FROM application__v")

    assert result["responseStatus"] == "FAILURE"

    mock_invalidate.assert_called_once()


@patch("lambda_function._vault_query")
def test_country_cache_failure(mock_query):

    lambda_function._country_id_map = None

    mock_query.return_value = {"responseStatus": "FAILURE"}

    result = lambda_function._get_country_id_map()

    assert result == {}


@patch("lambda_function._fn_get_regions")
def test_dispatch_regions(mock_fn):

    mock_fn.return_value = "OK"

    def p(name):
        return ""

    result = lambda_function._dispatch("getRegions", p)

    assert result == "OK"


def test_lambda_handler_empty_event():

    result = lambda_function.lambda_handler({}, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert "Unknown function" in body


@patch("lambda_function._name_exists")
def test_suggest_application_multiple_duplicates(mock_exists):

    mock_exists.side_effect = [True, True, False]

    result = lambda_function._fn_suggest_application_name("Drug", "NDA", "India")

    assert "_V3" in result


@patch("lambda_function._authenticate")
def test_get_session_auth_failure(mock_auth):

    lambda_function._session_id = None

    mock_auth.side_effect = RuntimeError("Authentication failed")

    with pytest.raises(RuntimeError):
        lambda_function.get_session_id()


@patch("lambda_function._http_get")
@patch("lambda_function.get_session_id")
def test_vault_query_failure(mock_session, mock_http):

    mock_session.return_value = "SESSION"

    mock_http.return_value = {
        "responseStatus": "FAILURE",
        "errors": [{"type": "OTHER_ERROR"}],
    }

    result = lambda_function._vault_query("SELECT id FROM application__v")

    assert result["responseStatus"] == "FAILURE"


def test_all_pages_failure_response():

    response = {"responseStatus": "FAILURE", "data": []}

    result = lambda_function._all_pages(response)

    assert result == []


def test_lambda_handler_invalid_parameters():

    event = {
        "actionGroup": "VaultQueries",
        "function": "queryVault",
        "parameters": [{"name": "queryType", "value": ""}],
    }

    result = lambda_function.lambda_handler(event, None)

    body = result["response"]["functionResponse"]["responseBody"]["TEXT"]["body"]

    assert "queryType" in body
