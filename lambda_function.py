# import libraries
import json
import os
import re
import datetime
import logging
import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Authenticating with Vault API using environment variables
VAULT_URL = os.environ.get(
    "VAULT_URL", "https://partnersi-cognizant-rim.veevavault.com"
)
VAULT_USERNAME = os.environ.get("VAULT_USERNAME", "gileadrim@partnersi-cognizant.com")
VAULT_PASSWORD = os.environ.get("VAULT_PASSWORD", "Gilead!101")
VAULT_API_VERSION = os.environ.get("VAULT_API_VERSION", "v24.1")

_session_id = None


def get_session_id():
    global _session_id
    if _session_id:
        return _session_id
    _session_id = _authenticate()
    return _session_id


def _authenticate():
    auth_url = f"{VAULT_URL}/api/{VAULT_API_VERSION}/auth"
    auth_data = urllib.parse.urlencode(
        {"username": VAULT_USERNAME, "password": VAULT_PASSWORD}
    ).encode("utf-8")
    auth_req = urllib.request.Request(
        auth_url,
        data=auth_data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(auth_req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        result = json.loads(e.read().decode("utf-8"))
    session_id = result.get("sessionId")
    if not session_id:
        raise RuntimeError(f"Vault auth failed: {json.dumps(result)[:300]}")
    return session_id


def _invalidate_session():
    global _session_id
    _session_id = None


def _http_get(url, headers):
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode())


def _is_success(result):
    return result.get("responseStatus") in ("SUCCESS", "WARNING")


def _vault_query(vql, retry=True):
    sid = get_session_id()
    url = f"{VAULT_URL}/api/{VAULT_API_VERSION}/query?q={urllib.parse.quote(vql)}"
    result = _http_get(url, {"Authorization": sid, "Accept": "application/json"})
    if result.get("responseStatus") == "FAILURE":
        errors = result.get("errors", [])
        if retry and any(e.get("type") == "INVALID_SESSION_ID" for e in errors):
            _invalidate_session()
            return _vault_query(vql, retry=False)
    return result


def _vault_next_page(next_page_url, retry=True):
    sid = get_session_id()
    url = f"{VAULT_URL}{next_page_url}"
    result = _http_get(url, {"Authorization": sid})
    if result.get("responseStatus") == "FAILURE":
        errors = result.get("errors", [])
        if retry and any(e.get("type") == "INVALID_SESSION_ID" for e in errors):
            _invalidate_session()
            return _vault_next_page(next_page_url, retry=False)
    return result


def _vault_get(path, retry=True):
    sid = get_session_id()
    url = f"{VAULT_URL}/api/{VAULT_API_VERSION}{path}"
    result = _http_get(url, {"Authorization": sid})
    if result.get("responseStatus") == "FAILURE":
        errors = result.get("errors", [])
        if retry and any(e.get("type") == "INVALID_SESSION_ID" for e in errors):
            _invalidate_session()
            return _vault_get(path, retry=False)
    return result


def _all_pages(first_result):
    records = list(first_result.get("data", []))
    next_page = first_result.get("responseDetails", {}).get("next_page", "")
    while next_page:
        result = _vault_next_page(next_page)
        if not _is_success(result):
            break
        records.extend(result.get("data", []))
        next_page = result.get("responseDetails", {}).get("next_page", "")
    return records


# ── ID map caches ──────────────────────────────────────────────────────────────

_country_id_map = None
_app_type_id_map = None
_dossier_format_id_map = None
_region_id_map = None
_ha_id_map = None
_ha_center_id_map = None


def _get_country_id_map():
    global _country_id_map
    if _country_id_map is not None:
        return _country_id_map
    _country_id_map = {}
    try:
        result = _vault_query("SELECT id, name__v FROM country__v ORDER BY name__v ASC")
        if _is_success(result):
            for r in _all_pages(result):
                cid, name = r.get("id", ""), r.get("name__v", "")
                if cid and name:
                    _country_id_map[cid] = name
    except Exception as e:
        logger.error(f"_get_country_id_map failed: {e}")
    return _country_id_map


def _get_app_type_id_map():
    global _app_type_id_map
    if _app_type_id_map is not None:
        return _app_type_id_map
    _app_type_id_map = {}
    try:
        result = _vault_query(
            "SELECT id, name__v FROM controlled_vocabulary__rim "
            "WHERE controlled_vocabulary_type__rim = 'application_type__rim' "
            "AND status__v != 'inactive__v' ORDER BY name__v ASC"
        )
        if _is_success(result):
            for r in _all_pages(result):
                tid, name = r.get("id", ""), r.get("name__v", "")
                if tid and name:
                    _app_type_id_map[tid] = name
    except Exception as e:
        logger.error(f"_get_app_type_id_map failed: {e}")
    return _app_type_id_map


def _get_dossier_format_id_map():
    global _dossier_format_id_map
    if _dossier_format_id_map is not None:
        return _dossier_format_id_map
    _dossier_format_id_map = {}
    try:
        result = _vault_query(
            "SELECT id, name__v FROM controlled_vocabulary__rim "
            "WHERE controlled_vocabulary_type__rim = 'dossier_format__v' "
            "AND status__v != 'inactive__v' ORDER BY name__v ASC"
        )
        if _is_success(result):
            for r in _all_pages(result):
                fid, name = r.get("id", ""), r.get("name__v", "")
                if fid and name:
                    _dossier_format_id_map[fid] = name
    except Exception as e:
        logger.error(f"_get_dossier_format_id_map failed: {e}")
    return _dossier_format_id_map


def _get_region_id_map():
    global _region_id_map
    if _region_id_map is not None:
        return _region_id_map
    _region_id_map = {}
    try:
        result = _vault_query("SELECT id, name__v FROM region__v ORDER BY name__v ASC")
        if _is_success(result):
            for r in _all_pages(result):
                rid, name = r.get("id", ""), r.get("name__v", "")
                if rid and name:
                    _region_id_map[rid] = name
    except Exception as e:
        logger.error(f"_get_region_id_map failed: {e}")
    return _region_id_map


def _get_ha_id_map():
    global _ha_id_map
    if _ha_id_map is not None:
        return _ha_id_map
    _ha_id_map = {}
    try:
        result = _vault_query(
            "SELECT id, name__v FROM health_authority__rim ORDER BY name__v ASC"
        )
        if _is_success(result):
            for r in _all_pages(result):
                hid, name = r.get("id", ""), r.get("name__v", "")
                if hid and name:
                    _ha_id_map[hid] = name
    except Exception as e:
        logger.error(f"_get_ha_id_map failed: {e}")
    return _ha_id_map


def _get_ha_center_id_map():
    global _ha_center_id_map
    if _ha_center_id_map is not None:
        return _ha_center_id_map
    _ha_center_id_map = {}
    try:
        result = _vault_query(
            "SELECT id, name__v FROM health_authority_division__v ORDER BY name__v ASC"
        )
        if _is_success(result):
            for r in _all_pages(result):
                cid, name = r.get("id", ""), r.get("name__v", "")
                if cid and name:
                    _ha_center_id_map[cid] = name
    except Exception as e:
        logger.error(f"_get_ha_center_id_map failed: {e}")
    return _ha_center_id_map


def _resolve_country(cid):
    return _get_country_id_map().get(cid, cid)


def _resolve_type(tid):
    return _get_app_type_id_map().get(tid, tid) if tid else "N/A"


def _resolve_format(fid):
    return _get_dossier_format_id_map().get(fid, fid) if fid else "N/A"


def _resolve_region(rid):
    return _get_region_id_map().get(rid, rid) if rid else "N/A"


def _resolve_ha(hid):
    return _get_ha_id_map().get(hid, hid) if hid else "N/A"


# ── Country fuzzy matching ─────────────────────────────────────────────────────

_COUNTRY_ALIASES = {
    # United States
    "usa": "united states",
    "us": "united states",
    # United Kingdom
    "uk": "united kingdom",
    "gb": "united kingdom",
    "britain": "united kingdom",
    "greatbritain": "united kingdom",
    "england": "united kingdom",
    # UAE
    "uae": "united arab emirates",
    # New Zealand — correct + common typos
    "nz": "new zealand",
    "newzealand": "new zealand",
    "newzealend": "new zealand",
    "newzeland": "new zealand",
    "newziland": "new zealand",
    "newzealnd": "new zealand",
    # Korea
    "southkorea": "korea",
    "northkorea": "korea",
    "rok": "korea",
    # Others
    "czechrepublic": "czech",
    "drc": "congo",
    "holland": "netherlands",
    "russia": "russian federation",
    "vietnam": "viet nam",
    "iran": "iran (islamic republic",
    "syria": "syrian arab republic",
    "tanzania": "united republic of tanzania",
    "bolivia": "bolivia (plurinational",
    "venezuela": "venezuela (bolivarian",
}

# ISO-style abbreviations for multi-word countries
_MARKET_ABBREV = {
    "new zealand": "NZL",
    "united states": "USA",
    "united kingdom": "GBR",
    "united arab emirates": "UAE",
    "south korea": "KOR",
    "north korea": "PRK",
    "saudi arabia": "SAU",
    "south africa": "ZAF",
    "czech republic": "CZE",
    "costa rica": "CRI",
    "hong kong": "HKG",
    "sri lanka": "LKA",
    "russian federation": "RUS",
    "viet nam": "VNM",
    "el salvador": "SLV",
    "puerto rico": "PRI",
}


def _normalize(s):
    """Lowercase + remove spaces, hyphens, underscores, dots, parentheses."""
    return re.sub(r"[\s\-_\.\(\),']+", "", s.lower().strip())


def _find_country_id(lead_market):
    raw = lead_market.strip()
    norm = _normalize(raw)
    alias = _COUNTRY_ALIASES.get(norm, norm)
    country_map = _get_country_id_map()

    # 1) Exact case-insensitive
    for cid, name in country_map.items():
        if name.lower() == raw.lower():
            return cid
    # 2) Normalized no-space  →  "Newzealand" == "New Zealand"
    for cid, name in country_map.items():
        if _normalize(name) == norm:
            return cid
    # 3) Alias  →  "USA"→"united states", "newzeland"→"new zealand"
    if alias != norm:
        for cid, name in country_map.items():
            if _normalize(name) == alias or alias in _normalize(name):
                return cid
    # 4) Starts-with
    for cid, name in country_map.items():
        if name.lower().startswith(raw.lower()):
            return cid
    # 5) Contains
    for cid, name in country_map.items():
        if raw.lower() in name.lower() or name.lower() in raw.lower():
            return cid

    logger.warning(f"_find_country_id: no match for '{lead_market}'")
    return None


def _market_abbrev(lead_market):
    """Return 3-letter abbreviation for country name."""
    return (
        _MARKET_ABBREV.get(lead_market.lower().strip())
        or lead_market.split()[0][:3].upper()
    )


# ── Fallback messages ──────────────────────────────────────────────────────────


def _no_vault_data(country, data_type):
    return (
        f"VAULT_NO_RECORDS for {country}. "
        f"There are no existing application records for {country} in this organization's Vault. "
        f"Use your general regulatory knowledge to answer the {data_type} question for {country}. "
        f"Start your response with: 'There are no existing records in your Vault for {country}.'"
    )


def _country_not_in_vault(country):
    return (
        f"VAULT_NO_RECORDS for {country}. "
        f"No records found for '{country}' in Vault. "
        f"Use your general regulatory knowledge to answer. "
        f"Start your response with: 'There are no existing records in your Vault for {country}.'"
    )


# ── Business logic ─────────────────────────────────────────────────────────────


def _find_application_type(user_input):
    lower = user_input.lower().strip()
    for name in _get_app_type_id_map().values():
        if name.lower() == lower:
            return name
    for name in _get_app_type_id_map().values():
        if f"({lower})" in name.lower():
            return name
    for name in _get_app_type_id_map().values():
        if lower in name.lower():
            return name
    return None


def _resolve_ha_id(user_input):
    lower = user_input.lower().strip()
    for hid, name in _get_ha_id_map().items():
        if name.lower() == lower:
            return hid, name
    for hid, name in _get_ha_id_map().items():
        if lower in name.lower():
            return hid, name
    return None, None


def _name_exists(name):
    result = _vault_query(f"SELECT id FROM application__v WHERE name__v = '{name}'")
    if not _is_success(result):
        return False
    return len(result.get("data", [])) > 0


def _fn_get_lead_markets():
    try:
        result = _vault_query(
            "SELECT lead_market__rim FROM application__v ORDER BY created_date__v DESC LIMIT 200"
        )
        if not _is_success(result):
            return "Unable to retrieve Lead Markets from Vault."
        country_map = _get_country_id_map()
        markets = sorted(
            set(
                country_map[r.get("lead_market__rim", "")]
                for r in result.get("data", [])
                if r.get("lead_market__rim", "") in country_map
            )
        )
        if not markets:
            return "No Lead Markets found in Vault."
        return (
            "The following Lead Markets already have existing applications in Vault: "
            + ", ".join(markets)
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_lead_markets: {e}")
        return "Unable to retrieve Lead Markets at this time."


def _fn_get_product_families():
    """
    Query product__v object (Vault Admin shows 'Product Family' → API name = product__v).
    Returns actual Vault product family records like 'Adalimumab Biosimilar', 'Metformin HCL'.
    Falls back to legacy variants if primary query fails.
    """
    # Primary: product__v is the correct API name for "Product Family" in this Vault
    candidates = [
        "product__v",  # ✅ confirmed API name in Vault Admin: Product Family → product__v
        "product_family__c",  # custom object fallback
        "product_family__rim",  # RIM application object fallback
        "product_family__v",  # standard object fallback
    ]
    for obj_name in candidates:
        try:
            result = _vault_query(
                f"SELECT id, name__v FROM {obj_name} ORDER BY name__v ASC"
            )
            logger.info(
                f"product_family query [{obj_name}] → responseStatus={result.get('responseStatus')} "
                f"errors={result.get('errors','none')}"
            )
            if _is_success(result):
                values = [
                    r.get("name__v", "")
                    for r in _all_pages(result)
                    if r.get("name__v", "")
                ]
                if values:
                    logger.info(
                        f"{obj_name} returned {len(values)} records: {values[:5]}"
                    )
                    return (
                        "Available Product Families in Vault: "
                        + ", ".join(values)
                        + "."
                    )
                logger.warning(f"{obj_name} query succeeded but returned 0 records")
        except Exception as e:
            logger.error(f"_fn_get_product_families [{obj_name}]: {e}")

    logger.warning("No product families found from any object name variant")
    return (
        "VAULT_NO_RECORDS for Product Families. "
        "No Product Family records were found in Vault. "
        "Do NOT say the answer came from Vault. "
        "Tell the user the available options could not be loaded from their Vault."
    )


def _fn_get_dossier_format_for_market(lead_market):
    try:
        country_id = _find_country_id(lead_market)
        if not country_id:
            return _country_not_in_vault(lead_market)
        result = _vault_query(
            f"SELECT dossier_format__v FROM application__v "
            f"WHERE lead_market__rim = '{country_id}' ORDER BY created_date__v DESC LIMIT 10"
        )
        if not _is_success(result):
            return _no_vault_data(lead_market, "dossier format")
        formats = list(
            dict.fromkeys(
                _resolve_format(r.get("dossier_format__v", ""))
                for r in result.get("data", [])
                if r.get("dossier_format__v", "")
            )
        )
        if not formats:
            return _no_vault_data(lead_market, "dossier format")
        if len(formats) == 1:
            return f"For {lead_market}, existing applications use the '{formats[0]}' Dossier Format."
        return (
            f"For {lead_market}, existing applications have used: "
            + ", ".join(formats)
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_dossier_format_for_market: {e}")
        return _no_vault_data(lead_market, "dossier format")


def _fn_get_dossier_formats():
    try:
        fmt_map = _get_dossier_format_id_map()
        if not fmt_map:
            return "No Dossier Format values found in Vault."
        return (
            "Available Dossier Formats in Vault: " + ", ".join(fmt_map.values()) + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_dossier_formats: {e}")
        return "Unable to retrieve Dossier Formats at this time."


def _fn_get_application_types_for_market(lead_market):
    try:
        country_id = _find_country_id(lead_market)
        if not country_id:
            return _country_not_in_vault(lead_market)
        result = _vault_query(
            f"SELECT application_type__rim FROM application__v "
            f"WHERE lead_market__rim = '{country_id}' ORDER BY created_date__v DESC LIMIT 10"
        )
        if not _is_success(result):
            return _no_vault_data(lead_market, "application type")
        types = list(
            dict.fromkeys(
                _resolve_type(r.get("application_type__rim", ""))
                for r in result.get("data", [])
                if r.get("application_type__rim", "")
            )
        )
        if not types:
            return _no_vault_data(lead_market, "application type")
        return (
            f"For {lead_market}, existing applications have used: "
            + ", ".join(types)
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_application_types_for_market: {e}")
        return _no_vault_data(lead_market, "application type")


def _fn_search_application_type(keyword):
    try:
        lower = keyword.lower().strip()
        matches = [
            name
            for name in _get_app_type_id_map().values()
            if f"({lower})" in name.lower() or lower in name.lower()
        ]
        if not matches:
            return f"No Application Types found matching '{keyword}' in Vault."
        return (
            f"Found {len(matches)} Application Type(s) matching '{keyword}': "
            + ", ".join(matches)
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_search_application_type: {e}")
        return "Unable to search Application Types at this time."


def _fn_validate_application_type_for_market(app_type, lead_market):
    try:
        matched_type = _find_application_type(app_type)
        if not matched_type:
            return f"'{app_type}' is not a recognised Application Type in Vault."
        country_id = _find_country_id(lead_market)
        if not country_id:
            return (
                f"'{matched_type}' is a valid Application Type in Vault. "
                f"Use your general regulatory knowledge to confirm if it applies to {lead_market}."
            )
        result = _vault_query(
            f"SELECT application_type__rim FROM application__v "
            f"WHERE lead_market__rim = '{country_id}' ORDER BY created_date__v DESC LIMIT 10"
        )
        market_types = list(
            dict.fromkeys(
                _resolve_type(r.get("application_type__rim", ""))
                for r in result.get("data", [])
                if r.get("application_type__rim", "")
            )
        )
        if not market_types:
            return (
                f"'{matched_type}' is a valid Application Type in Vault. "
                f"No existing {lead_market} applications found in Vault to confirm. "
                f"Use your general regulatory knowledge to confirm if it is appropriate for {lead_market}."
            )
        if any(t.lower() == matched_type.lower() for t in market_types):
            return f"Yes — '{matched_type}' has been used in existing {lead_market} applications in Vault."
        return (
            f"'{matched_type}' is valid in Vault but not used in existing {lead_market} applications. "
            f"Common types for {lead_market} in Vault: " + ", ".join(market_types) + "."
        )
    except Exception as e:
        logger.error(f"_fn_validate_application_type_for_market: {e}")
        return "Unable to validate Application Type at this time."


def _fn_get_regions():
    try:
        region_map = _get_region_id_map()
        if not region_map:
            return "No regions found in Vault."
        return "Available Regions in Vault: " + ", ".join(region_map.values()) + "."
    except Exception as e:
        logger.error(f"_fn_get_regions: {e}")
        return "Unable to retrieve regions at this time."


def _fn_get_region_for_market(lead_market):
    try:
        country_id = _find_country_id(lead_market)
        if not country_id:
            return _country_not_in_vault(lead_market)
        result = _vault_query(
            f"SELECT region__v FROM application__v "
            f"WHERE lead_market__rim = '{country_id}' ORDER BY created_date__v DESC LIMIT 5"
        )
        if not _is_success(result):
            return _no_vault_data(lead_market, "region")
        regions = list(
            dict.fromkeys(
                _resolve_region(r.get("region__v", ""))
                for r in result.get("data", [])
                if r.get("region__v", "") and r.get("region__v", "") != "null"
            )
        )
        if not regions:
            return _no_vault_data(lead_market, "region")
        return f"For {lead_market}, the region is '{regions[0]}'."
    except Exception as e:
        logger.error(f"_fn_get_region_for_market: {e}")
        return _no_vault_data(lead_market, "region")


def _fn_get_countries_for_region(region_name):
    try:
        region_map = _get_region_id_map()
        lower = region_name.lower().strip()
        region_id, resolved = None, region_name
        for rid, name in region_map.items():
            if name.lower() == lower or lower in name.lower():
                region_id, resolved = rid, name
                break
        if not region_id:
            return (
                f"Region '{region_name}' not found. Available: "
                + ", ".join(region_map.values())
                + "."
            )
        result = _vault_query(
            f"SELECT id, name__v FROM country__v WHERE region__v = '{region_id}' ORDER BY name__v ASC"
        )
        countries = [
            r.get("name__v", "") for r in _all_pages(result) if r.get("name__v", "")
        ]
        if not countries:
            return f"No countries found for region '{resolved}' in Vault."
        return f"Countries in {resolved}: " + ", ".join(countries) + "."
    except Exception as e:
        logger.error(f"_fn_get_countries_for_region: {e}")
        return "Unable to retrieve countries at this time."


def _fn_get_all_health_authorities():
    try:
        ha_map = _get_ha_id_map()
        if not ha_map:
            return "No Health Authorities found in Vault."
        return (
            f"There are {len(ha_map)} Health Authorities in Vault: "
            + ", ".join(ha_map.values())
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_all_health_authorities: {e}")
        return "Unable to retrieve Health Authorities at this time."


def _fn_get_health_authority_for_market(lead_market):
    try:
        country_id = _find_country_id(lead_market)
        if not country_id:
            return _country_not_in_vault(lead_market)
        result = _vault_query(
            f"SELECT health_authority__v FROM application__v "
            f"WHERE lead_market__rim = '{country_id}' ORDER BY created_date__v DESC LIMIT 10"
        )
        if not _is_success(result):
            return _no_vault_data(lead_market, "health authority")
        authorities = list(
            dict.fromkeys(
                _resolve_ha(r.get("health_authority__v", ""))
                for r in result.get("data", [])
                if r.get("health_authority__v", "")
            )
        )
        if not authorities:
            return _no_vault_data(lead_market, "health authority")
        if len(authorities) == 1:
            return f"For {lead_market}, the Health Authority is: {authorities[0]}."
        return (
            f"For {lead_market}, Health Authorities in Vault: "
            + ", ".join(authorities)
            + "."
        )
    except Exception as e:
        logger.error(f"_fn_get_health_authority_for_market: {e}")
        return _no_vault_data(lead_market, "health authority")


def _fn_get_health_authority_centers(ha_name):
    try:
        ha_id, _ = _resolve_ha_id(ha_name)
        center_map = _get_ha_center_id_map()
        centers = []
        if ha_id:
            result = _vault_query(
                f"SELECT health_authority_center__v FROM application__v "
                f"WHERE health_authority__v = '{ha_id}' ORDER BY created_date__v DESC LIMIT 20"
            )
            if _is_success(result):
                centers = list(
                    dict.fromkeys(
                        center_map[r.get("health_authority_center__v", "")]
                        for r in result.get("data", [])
                        if r.get("health_authority_center__v", "") in center_map
                    )
                )
        if not centers:
            centers = list(center_map.values())
        if not centers:
            return (
                f"VAULT_NO_RECORDS for {ha_name} centers. "
                f"No Health Authority Center records found in Vault for '{ha_name}'. "
                f"Start your response with: 'There are no existing records in your Vault for {ha_name} centers.'"
            )
        return f"Health Authority Centers for {ha_name}: " + ", ".join(centers) + "."
    except Exception as e:
        logger.error(f"_fn_get_health_authority_centers: {e}")
        return "Unable to retrieve Health Authority Centers at this time."


def _fn_suggest_application_name(product_name, app_type, lead_market):
    try:
        year = datetime.datetime.now().year
        m = re.search(r"\(([^)]+)\)", app_type)
        type_abbrev = m.group(1) if m else app_type.replace(" ", "")
        mkt_abbrev = _market_abbrev(lead_market)
        base = f"{product_name}_{type_abbrev}_{mkt_abbrev}_{year}"
        if not _name_exists(base):
            return (
                f"Suggested Application Name: '{base}'. "
                f"This name does not exist in Vault yet — safe to use."
            )
        for i in range(2, 10):
            candidate = f"{base}_V{i}"
            if not _name_exists(candidate):
                return (
                    f"Suggested Application Name: '{candidate}'. "
                    f"This name does not exist in Vault yet — safe to use."
                )
        return f"Suggested Application Name: '{base}'."
    except Exception as e:
        logger.error(f"_fn_suggest_application_name: {e}")
        return "Unable to suggest an application name at this time."


def _fn_check_application_name(application_name):
    try:
        if _name_exists(application_name):
            return (
                f"'{application_name}' already exists in Vault — "
                f"choosing this name will create a duplicate. Please choose a different name."
            )
        return f"'{application_name}' does not exist in Vault yet — safe to use."
    except Exception as e:
        logger.error(f"_fn_check_application_name: {e}")
        return "Unable to check application name at this time."


def _fn_get_existing_applications(lead_market):
    try:
        country_id = _find_country_id(lead_market)
        if not country_id:
            return _country_not_in_vault(lead_market)
        result = _vault_query(
            f"SELECT name__v, application_type__rim, dossier_format__v, region__v, health_authority__v "
            f"FROM application__v WHERE lead_market__rim = '{country_id}' "
            f"ORDER BY created_date__v DESC LIMIT 5"
        )
        if not _is_success(result):
            return _no_vault_data(lead_market, "existing applications")
        summaries = [
            f"• {r.get('name__v','N/A')} | Type: {_resolve_type(r.get('application_type__rim',''))} | "
            f"Format: {_resolve_format(r.get('dossier_format__v',''))} | "
            f"Region: {_resolve_region(r.get('region__v',''))} | "
            f"HA: {_resolve_ha(r.get('health_authority__v',''))}"
            for r in result.get("data", [])
        ]
        if not summaries:
            return _no_vault_data(lead_market, "existing applications")
        return f"Recent applications for {lead_market} in Vault:\n" + "\n".join(
            summaries
        )
    except Exception as e:
        logger.error(f"_fn_get_existing_applications: {e}")
        return "Unable to retrieve existing applications at this time."


def _fn_get_recent_applications():
    try:
        result = _vault_query(
            "SELECT name__v, application_type__rim, dossier_format__v, lead_market__rim, region__v "
            "FROM application__v ORDER BY created_date__v DESC LIMIT 5"
        )
        if not _is_success(result):
            return "Unable to retrieve applications from Vault."
        country_map = _get_country_id_map()
        summaries = [
            f"• {r.get('name__v','N/A')} | Market: {country_map.get(r.get('lead_market__rim',''), 'N/A')} | "
            f"Type: {_resolve_type(r.get('application_type__rim',''))} | "
            f"Format: {_resolve_format(r.get('dossier_format__v',''))}"
            for r in result.get("data", [])
        ]
        if not summaries:
            return "No existing applications found in Vault."
        return "Most recent applications in Vault:\n" + "\n".join(summaries)
    except Exception as e:
        logger.error(f"_fn_get_recent_applications: {e}")
        return "Unable to retrieve recent applications at this time."


# ── Main Handler ───────────────────────────────────────────────────────────────


def lambda_handler(event, context):

    # Handle Lambda warmup request
    if event.get("warmup"):
        return {"statusCode": 200, "body": '{"status":"warm"}'}

    action_group = event.get("actionGroup", "VaultQueries")
    function = event.get("function", "")
    parameters = event.get("parameters", [])

    logger.info(f"BEDROCK CALL → actionGroup={action_group}, function={function}")
    logger.info(f"RAW PARAMS → {json.dumps(parameters)}")

    def get_param(name):
        for p in parameters:
            if p.get("name") == name:
                return str(p.get("value", "") or "").strip()
        return ""

    try:
        if function == "queryVault":
            query_type = get_param("queryType")
            params_str = get_param("parameters")
            logger.info(
                f"queryVault → queryType={query_type!r}, parameters={params_str!r}"
            )

            try:
                params_dict = json.loads(params_str) if params_str else {}
            except Exception:
                params_dict = {}

            def p(name):
                for k, v in params_dict.items():
                    if k.lower() == name.lower():
                        val = str(v).strip()
                        if val:
                            return val
                raise ValueError(f"Missing parameter: {name}")

            if not query_type:
                response_text = "Error: queryType was not provided."
            else:
                response_text = _dispatch(query_type, p)
        else:
            logger.error(f"Unknown function: {function!r}")
            response_text = f"Unknown function: {function}"

    except Exception as e:
        logger.error(f"Handler error: {e}")
        response_text = (
            "I encountered an error retrieving data from Vault. Please try again."
        )

    logger.info(f"RESPONSE → {response_text[:300]}")

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action_group,
            "function": function,
            "functionResponse": {"responseBody": {"TEXT": {"body": response_text}}},
        },
    }


def _dispatch(query_type, p):
    if query_type == "getLeadMarkets":
        return _fn_get_lead_markets()
    elif query_type == "getProductFamilies":
        return _fn_get_product_families()
    elif query_type == "getDossierFormats":
        return _fn_get_dossier_formats()
    elif query_type == "getDossierFormatForMarket":
        return _fn_get_dossier_format_for_market(p("leadMarket"))
    elif query_type == "getApplicationTypesForMarket":
        return _fn_get_application_types_for_market(p("leadMarket"))
    elif query_type == "searchApplicationType":
        return _fn_search_application_type(p("searchKeyword"))
    elif query_type == "validateApplicationTypeForMarket":
        return _fn_validate_application_type_for_market(
            p("applicationType"), p("leadMarket")
        )
    elif query_type == "getRegions":
        return _fn_get_regions()
    elif query_type == "getRegionForMarket":
        return _fn_get_region_for_market(p("leadMarket"))
    elif query_type == "getCountriesForRegion":
        return _fn_get_countries_for_region(p("region"))
    elif query_type == "getAllHealthAuthorities":
        return _fn_get_all_health_authorities()
    elif query_type == "getHealthAuthorityForMarket":
        return _fn_get_health_authority_for_market(p("leadMarket"))
    elif query_type == "getHealthAuthorityCenters":
        return _fn_get_health_authority_centers(p("haName"))
    elif query_type == "suggestApplicationName":
        return _fn_suggest_application_name(
            p("productName"), p("applicationType"), p("leadMarket")
        )
    elif query_type == "checkApplicationNameExists":
        return _fn_check_application_name(p("applicationName"))
    elif query_type == "getExistingApplications":
        return _fn_get_existing_applications(p("leadMarket"))
    elif query_type == "getRecentApplications":
        return _fn_get_recent_applications()
    else:
        logger.error(f"Unknown queryType: {query_type!r}")
        return (
            f"Unknown queryType '{query_type}'. Valid values: getLeadMarkets, getProductFamilies, "
            "getDossierFormats, getDossierFormatForMarket, getApplicationTypesForMarket, "
            "searchApplicationType, validateApplicationTypeForMarket, getRegions, "
            "getRegionForMarket, getCountriesForRegion, getAllHealthAuthorities, "
            "getHealthAuthorityForMarket, getHealthAuthorityCenters, suggestApplicationName, "
            "checkApplicationNameExists, getExistingApplications, getRecentApplications."
        )
