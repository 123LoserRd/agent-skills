#!/usr/bin/env python3
"""Create and publish resale listings (photos, title, description, price) on eBay and Depop.

Uses only the Python standard library and the official APIs:
  - eBay: Sell Inventory API, Commerce Media API, Taxonomy API, Account API (OAuth user token)
  - Depop: Selling API (approved partners only, OAuth bearer token)

Secrets are read from environment variables and a local token store (0600); they are never printed.
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import secrets
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlencode
from urllib.request import Request, urlopen

TOKEN_HOME = Path(os.environ.get("RESALE_LISTER_HOME", Path.home() / ".config" / "resale-lister"))
TOKEN_FILE = TOKEN_HOME / "tokens.json"

EBAY_HOSTS = {
    "production": {"api": "https://api.ebay.com", "apim": "https://apim.ebay.com",
                   "auth": "https://auth.ebay.com", "web": "https://www.ebay.com"},
    "sandbox": {"api": "https://api.sandbox.ebay.com", "apim": "https://apim.sandbox.ebay.com",
                "auth": "https://auth.sandbox.ebay.com", "web": "https://sandbox.ebay.com"},
}
EBAY_USER_SCOPES = [
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
]
EBAY_APP_SCOPE = "https://api.ebay.com/oauth/api_scope"
EBAY_LANGUAGE = {
    "EBAY_US": "en-US", "EBAY_GB": "en-GB", "EBAY_AU": "en-AU", "EBAY_CA": "en-CA", "EBAY_IE": "en-IE",
    "EBAY_DE": "de-DE", "EBAY_AT": "de-AT", "EBAY_FR": "fr-FR", "EBAY_IT": "it-IT", "EBAY_ES": "es-ES",
}
EBAY_MAX_PHOTOS = 24
EBAY_MAX_TITLE = 80

DEPOP_HOSTS = {"production": "https://partnerapi.depop.com", "staging": "https://partnerapi-staging.depop.com"}
DEPOP_MAX_PHOTOS = 8
DEPOP_DEPARTMENTS = {"womenswear", "menswear", "kidswear", "everything-else"}

# Canonical condition -> (eBay inventory ConditionEnum, Depop condition)
CONDITIONS = {
    "new": ("NEW", "brand_new"),
    "new_other": ("NEW_OTHER", "used_like_new"),
    "like_new": ("LIKE_NEW", "used_like_new"),
    "used_excellent": ("USED_EXCELLENT", "used_excellent"),
    "used_good": ("USED_GOOD", "used_good"),
    "used_fair": ("USED_ACCEPTABLE", "used_fair"),
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class ListingError(Exception):
    pass


# --------------------------------------------------------------------------- utilities


def env(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise ListingError(f"Missing environment variable {name}. Set it locally (never paste secrets into chat).")
    return value


def load_tokens():
    try:
        return json.loads(TOKEN_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_tokens(data):
    TOKEN_HOME.mkdir(parents=True, exist_ok=True)
    tmp = TOKEN_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, TOKEN_FILE)


def http(method, url, headers=None, body=None, retries=1):
    """Perform an HTTP request. Returns (status, headers, parsed_json_or_None)."""
    data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
    headers = dict(headers or {})
    if data is not None and not isinstance(body, bytes):
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    attempt = 0
    while True:
        req = Request(url, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=60) as resp:
                raw = resp.read()
                parsed = json.loads(raw) if raw.strip() else None
                return resp.status, dict(resp.headers), parsed
        except HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < retries:
                attempt += 1
                time.sleep(2 * attempt)
                continue
            raise ApiError(exc.code, exc.read().decode(errors="replace")[:2000]) from None
        except URLError as exc:
            if attempt < retries:
                attempt += 1
                time.sleep(2 * attempt)
                continue
            raise ApiError(0, f"network error: {exc.reason}") from None


def emit(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def is_url(value):
    return value.startswith("https://") or value.startswith("http://")


# --------------------------------------------------------------------------- listing model


def load_listing(path):
    path = Path(path)
    try:
        listing = json.loads(path.read_text())
    except FileNotFoundError:
        raise ListingError(f"{path}: file not found") from None
    except json.JSONDecodeError as exc:
        raise ListingError(f"{path}: invalid JSON ({exc})") from None
    listing["_base_dir"] = str(path.resolve().parent)
    listing["_file"] = str(path)
    return listing


def photo_sources(listing):
    """Return photos as absolute local paths or URLs."""
    result = []
    for photo in listing.get("photos", []):
        if is_url(photo):
            result.append(photo)
        else:
            p = Path(photo).expanduser()
            result.append(str(p if p.is_absolute() else Path(listing["_base_dir"]) / p))
    return result


def validate(listing, targets):
    errors, warnings = [], []
    for field in ("sku", "title", "description", "price", "currency", "condition", "photos"):
        if not listing.get(field):
            errors.append(f"'{field}' is required")
    sku = str(listing.get("sku", ""))
    if len(sku) > 50:
        errors.append("'sku' must be at most 50 characters")
    if listing.get("condition") and listing["condition"] not in CONDITIONS:
        errors.append(f"'condition' must be one of {sorted(CONDITIONS)}")
    try:
        if float(listing.get("price", 0)) < 1:
            errors.append("'price' must be at least 1.00")
    except (TypeError, ValueError):
        errors.append("'price' must be a number string like \"25.00\"")
    if not isinstance(listing.get("quantity", 1), int) or listing.get("quantity", 1) < 1:
        errors.append("'quantity' must be an integer >= 1")
    for photo in photo_sources(listing):
        if not is_url(photo) and not Path(photo).is_file():
            errors.append(f"photo not found: {photo}")

    if "ebay" in targets:
        ebay = listing.get("ebay", {})
        if len(listing.get("title", "")) > EBAY_MAX_TITLE:
            errors.append(f"eBay: 'title' must be at most {EBAY_MAX_TITLE} characters")
        if len(listing.get("photos", [])) > EBAY_MAX_PHOTOS:
            errors.append(f"eBay: at most {EBAY_MAX_PHOTOS} photos")
        for field in ("categoryId", "merchantLocationKey", "fulfillmentPolicyId", "paymentPolicyId", "returnPolicyId"):
            if not ebay.get(field):
                errors.append(f"eBay: 'ebay.{field}' is required (see `ebay policies` / `ebay suggest-category`)")
        if not ebay.get("aspects", {}).get("Brand") and listing.get("brand"):
            warnings.append("eBay: 'Brand' aspect will be filled from 'brand'")

    if "depop" in targets:
        depop = listing.get("depop", {})
        if len(listing.get("photos", [])) > DEPOP_MAX_PHOTOS:
            errors.append(f"Depop: at most {DEPOP_MAX_PHOTOS} photos (the first is the cover)")
        local = [p for p in listing.get("photos", []) if not is_url(p)]
        if local and not depop.get("photoBaseUrl"):
            errors.append("Depop: photos must be public HTTPS URLs; host them and set 'depop.photoBaseUrl' "
                          "(local file names are appended to it) or list URLs in 'photos'")
        if depop.get("department") not in DEPOP_DEPARTMENTS:
            errors.append(f"Depop: 'depop.department' must be one of {sorted(DEPOP_DEPARTMENTS)}")
        if not depop.get("product_type"):
            errors.append("Depop: 'depop.product_type' is required (e.g. tshirts, jeans, sneakers)")
        if not depop.get("address", {}).get("country_code"):
            errors.append("Depop: 'depop.address.country_code' is required")
        if bool(depop.get("national_shipping_cost") is not None) == bool(depop.get("depop_shipping")):
            errors.append("Depop: set exactly one of 'depop.national_shipping_cost' or 'depop.depop_shipping'")
        if not listing.get("brand"):
            warnings.append("Depop: no 'brand' given, 'unbranded' will be sent")
    return errors, warnings


# --------------------------------------------------------------------------- eBay


class Ebay:
    def __init__(self):
        self.env = os.environ.get("EBAY_ENV", "production").strip() or "production"
        if self.env not in EBAY_HOSTS:
            raise ListingError("EBAY_ENV must be 'production' or 'sandbox'")
        self.hosts = EBAY_HOSTS[self.env]
        self._user_token = None
        self._app_token = None

    def _basic(self):
        creds = f"{env('EBAY_CLIENT_ID')}:{env('EBAY_CLIENT_SECRET')}".encode()
        return "Basic " + base64.b64encode(creds).decode()

    def _token_request(self, form):
        _, _, body = http("POST", f"{self.hosts['api']}/identity/v1/oauth2/token",
                          headers={"Authorization": self._basic(),
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          body=urlencode(form).encode())
        return body

    def auth_url(self):
        query = urlencode({"client_id": env("EBAY_CLIENT_ID"), "response_type": "code",
                           "redirect_uri": env("EBAY_RU_NAME"), "scope": " ".join(EBAY_USER_SCOPES)})
        return f"{self.hosts['auth']}/oauth2/authorize?{query}"

    def exchange_code(self, code):
        body = self._token_request({"grant_type": "authorization_code", "code": unquote(code),
                                    "redirect_uri": env("EBAY_RU_NAME")})
        tokens = load_tokens()
        tokens.setdefault("ebay", {})[self.env] = {"refresh_token": body["refresh_token"],
                                                   "refresh_token_expires_in": body.get("refresh_token_expires_in")}
        save_tokens(tokens)
        return {"connected": True, "environment": self.env, "token_store": str(TOKEN_FILE)}

    def refresh_token(self):
        stored = load_tokens().get("ebay", {}).get(self.env, {}).get("refresh_token")
        token = os.environ.get("EBAY_REFRESH_TOKEN", "").strip() or stored
        if not token:
            raise ListingError("eBay is not connected. Run `listing.py ebay auth-url`, sign in, then "
                               "`listing.py ebay exchange-code --code ...` (or set EBAY_REFRESH_TOKEN).")
        return token

    def user_token(self):
        if not self._user_token:
            body = self._token_request({"grant_type": "refresh_token", "refresh_token": self.refresh_token(),
                                        "scope": " ".join(EBAY_USER_SCOPES)})
            self._user_token = body["access_token"]
        return self._user_token

    def app_token(self):
        if not self._app_token:
            body = self._token_request({"grant_type": "client_credentials", "scope": EBAY_APP_SCOPE})
            self._app_token = body["access_token"]
        return self._app_token

    def call(self, method, path, body=None, headers=None, app=False, host="api"):
        token = self.app_token() if app else self.user_token()
        hdrs = {"Authorization": f"Bearer {token}", **(headers or {})}
        return http(method, f"{self.hosts[host]}{path}", headers=hdrs, body=body)

    # ---- setup helpers

    def policies(self, marketplace):
        out = {"environment": self.env, "marketplaceId": marketplace}
        for kind, key, id_field in (("fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
                                    ("payment_policy", "paymentPolicies", "paymentPolicyId"),
                                    ("return_policy", "returnPolicies", "returnPolicyId")):
            _, _, body = self.call("GET", f"/sell/account/v1/{kind}?marketplace_id={marketplace}")
            out[key] = [{id_field: p[id_field], "name": p.get("name")} for p in (body or {}).get(key, [])]
        _, _, body = self.call("GET", "/sell/inventory/v1/location")
        out["locations"] = [{"merchantLocationKey": loc["merchantLocationKey"], "name": loc.get("name")}
                            for loc in (body or {}).get("locations", [])]
        return out

    def create_location(self, key, postal_code, country, name):
        payload = {"location": {"address": {"postalCode": postal_code, "country": country}},
                   "locationTypes": ["WAREHOUSE"], "name": name or key, "merchantLocationStatus": "ENABLED"}
        self.call("POST", f"/sell/inventory/v1/location/{quote(key, safe='')}", body=payload)
        return {"created": True, "merchantLocationKey": key}

    def _tree_id(self, marketplace):
        _, _, body = self.call("GET", f"/commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id={marketplace}",
                               app=True)
        return body["categoryTreeId"]

    def suggest_category(self, query, marketplace):
        tree = self._tree_id(marketplace)
        _, _, body = self.call("GET", f"/commerce/taxonomy/v1/category_tree/{tree}/get_category_suggestions?"
                               + urlencode({"q": query}), app=True)
        return [{"categoryId": s["category"]["categoryId"], "categoryName": s["category"]["categoryName"],
                 "path": " > ".join(a["categoryName"] for a in reversed(s.get("categoryTreeNodeAncestors", [])))}
                for s in (body or {}).get("categorySuggestions", [])[:10]]

    def category_info(self, category_id, marketplace):
        tree = self._tree_id(marketplace)
        _, _, body = self.call("GET", f"/commerce/taxonomy/v1/category_tree/{tree}/get_item_aspects_for_category?"
                               + urlencode({"category_id": category_id}), app=True)
        aspects = []
        for a in (body or {}).get("aspects", []):
            c = a.get("aspectConstraint", {})
            if c.get("aspectRequired") or c.get("aspectUsage") == "RECOMMENDED":
                aspects.append({"name": a["localizedAspectName"], "required": bool(c.get("aspectRequired")),
                                "values": [v["localizedValue"] for v in a.get("aspectValues", [])[:25]]})
        conditions = []
        try:
            _, _, cbody = self.call("GET", f"/sell/metadata/v1/marketplace/{marketplace}/get_item_condition_policies?"
                                    + urlencode({"filter": f"categoryIds:{{{category_id}}}"}), app=True)
            for policy in (cbody or {}).get("itemConditionPolicies", []):
                conditions = [{"conditionId": c["conditionId"], "description": c.get("conditionDescription")}
                              for c in policy.get("itemConditions", [])]
        except ApiError:
            pass
        return {"categoryId": category_id, "aspects": aspects, "conditions": conditions}

    # ---- listing

    def upload_photo(self, path):
        filename = Path(path).name
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{filename}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + Path(path).read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        _, headers, _ = self.call("POST", "/commerce/media/v1_beta/image/create_image_from_file", body=body,
                                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, host="apim")
        location = headers.get("Location") or headers.get("location")
        if not location:
            raise ListingError(f"eBay did not return an image location for {filename}")
        image_id = location.rstrip("/").rsplit("/", 1)[-1]
        _, _, image = self.call("GET", f"/commerce/media/v1_beta/image/{image_id}", host="apim")
        return image["imageUrl"]

    def payloads(self, listing, image_urls):
        ebay = listing.get("ebay", {})
        aspects = {k: v if isinstance(v, list) else [v] for k, v in ebay.get("aspects", {}).items()}
        if listing.get("brand"):
            aspects.setdefault("Brand", [listing["brand"]])
        condition = ebay.get("condition") or CONDITIONS[listing["condition"]][0]
        item = {
            "availability": {"shipToLocationAvailability": {"quantity": listing.get("quantity", 1)}},
            "condition": condition,
            "product": {"title": listing["title"], "description": listing["description"],
                        "aspects": aspects, "imageUrls": image_urls},
        }
        if ebay.get("conditionDescription") and not condition.startswith("NEW"):
            item["conditionDescription"] = ebay["conditionDescription"]
        marketplace = ebay.get("marketplaceId", "EBAY_US")
        offer = {
            "sku": listing["sku"], "marketplaceId": marketplace, "format": "FIXED_PRICE",
            "availableQuantity": listing.get("quantity", 1), "categoryId": str(ebay.get("categoryId", "")),
            "listingDescription": listing["description"],
            "listingPolicies": {"fulfillmentPolicyId": ebay.get("fulfillmentPolicyId"),
                                "paymentPolicyId": ebay.get("paymentPolicyId"),
                                "returnPolicyId": ebay.get("returnPolicyId")},
            "pricingSummary": {"price": {"value": str(listing["price"]), "currency": listing["currency"]}},
            "merchantLocationKey": ebay.get("merchantLocationKey"),
        }
        if ebay.get("bestOffer"):
            offer["listingPolicies"]["bestOfferTerms"] = {"bestOfferEnabled": True}
        return item, offer

    def find_offer(self, sku, marketplace):
        try:
            _, _, body = self.call("GET", "/sell/inventory/v1/offer?"
                                   + urlencode({"sku": sku, "marketplace_id": marketplace}))
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise
        offers = (body or {}).get("offers", [])
        return offers[0] if offers else None

    def publish(self, listing, dry_run=False):
        photos = photo_sources(listing)
        if dry_run:
            item, offer = self.payloads(listing, photos)
            return {"platform": "ebay", "dryRun": True, "environment": self.env, "inventoryItem": item, "offer": offer}
        image_urls = [p if is_url(p) else self.upload_photo(p) for p in photos]
        item, offer = self.payloads(listing, image_urls)
        sku = quote(listing["sku"], safe="")
        language = listing.get("ebay", {}).get("contentLanguage") or EBAY_LANGUAGE.get(offer["marketplaceId"], "en-US")
        lang = {"Content-Language": language}
        self.call("PUT", f"/sell/inventory/v1/inventory_item/{sku}", body=item, headers=lang)
        existing = self.find_offer(listing["sku"], offer["marketplaceId"])
        if existing:
            offer_id = existing["offerId"]
            self.call("PUT", f"/sell/inventory/v1/offer/{offer_id}", body=offer, headers=lang)
        else:
            _, _, created = self.call("POST", "/sell/inventory/v1/offer", body=offer, headers=lang)
            offer_id = created["offerId"]
        _, _, published = self.call("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")
        listing_id = published["listingId"]
        return {"platform": "ebay", "sku": listing["sku"], "offerId": offer_id, "listingId": listing_id,
                "url": f"{self.hosts['web']}/itm/{listing_id}", "updated": bool(existing)}

    def withdraw(self, sku, marketplace):
        offer = self.find_offer(sku, marketplace)
        if not offer:
            raise ListingError(f"No eBay offer found for SKU {sku}")
        self.call("POST", f"/sell/inventory/v1/offer/{offer['offerId']}/withdraw")
        return {"platform": "ebay", "sku": sku, "withdrawn": True}


# --------------------------------------------------------------------------- Depop


class Depop:
    def __init__(self):
        self.env = os.environ.get("DEPOP_ENV", "production").strip() or "production"
        if self.env not in DEPOP_HOSTS:
            raise ListingError("DEPOP_ENV must be 'production' or 'staging'")
        self.base = DEPOP_HOSTS[self.env]

    def _store_tokens(self, body):
        tokens = load_tokens()
        tokens.setdefault("depop", {})[self.env] = {
            "access_token": body["access_token"], "refresh_token": body.get("refresh_token"),
            "expires_at": int(time.time()) + int(body.get("expires_in", 3600)) - 60,
        }
        save_tokens(tokens)

    def _token_request(self, form):
        form = {"client_id": env("DEPOP_CLIENT_ID"), "client_secret": env("DEPOP_CLIENT_SECRET"), **form}
        _, _, body = http("POST", f"{self.base}/api/v1/oauth2/access-token/",
                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                          body=urlencode(form).encode())
        self._store_tokens(body)
        return body

    def exchange_code(self, code, verifier, redirect_uri):
        self._token_request({"grant_type": "authorization_code", "code": unquote(code),
                             "code_verifier": verifier, "redirect_uri": redirect_uri})
        return {"connected": True, "environment": self.env, "token_store": str(TOKEN_FILE)}

    def token(self):
        direct = os.environ.get("DEPOP_ACCESS_TOKEN", "").strip()
        if direct:
            return direct
        stored = load_tokens().get("depop", {}).get(self.env, {})
        if stored.get("access_token") and stored.get("expires_at", 0) > time.time():
            return stored["access_token"]
        # Depop refresh tokens are single-use: always prefer the rotated one in the store.
        refresh = stored.get("refresh_token") or os.environ.get("DEPOP_REFRESH_TOKEN", "").strip()
        if not refresh:
            raise ListingError("Depop is not connected. Set DEPOP_ACCESS_TOKEN, or DEPOP_REFRESH_TOKEN with "
                               "DEPOP_CLIENT_ID/DEPOP_CLIENT_SECRET, or run `listing.py depop exchange-code`.")
        return self._token_request({"grant_type": "refresh_token", "refresh_token": refresh})["access_token"]

    def call(self, method, path, body=None):
        return http(method, f"{self.base}{path}", headers={"Authorization": f"Bearer {self.token()}"}, body=body)

    def payload(self, listing):
        depop = dict(listing.get("depop", {}))
        base_url = depop.pop("photoBaseUrl", "").rstrip("/")
        pictures = [{"url": p if is_url(p) else f"{base_url}/{quote(Path(p).name)}"}
                    for p in listing.get("photos", [])]
        description = listing["description"]
        if listing.get("title") and not description.startswith(listing["title"]):
            description = f"{listing['title']}\n\n{description}"
        body = {
            "description": description, "price_amount": str(listing["price"]),
            "price_currency": listing["currency"], "quantity": listing.get("quantity", 1),
            "pictures": pictures, "condition": depop.pop("condition", None) or CONDITIONS[listing["condition"]][1],
            "brand_name": listing.get("brand") or "unbranded", "attributes": depop.pop("attributes", {}),
        }
        body.update({k: v for k, v in depop.items() if v is not None})
        return body

    def publish(self, listing, dry_run=False):
        body = self.payload(listing)
        if dry_run:
            return {"platform": "depop", "dryRun": True, "environment": self.env, "product": body}
        _, _, resp = self.call("PUT", f"/api/v1/products/by-sku/{quote(listing['sku'], safe='')}/", body=body)
        return {"platform": "depop", "sku": listing["sku"], "productId": resp.get("product_id"),
                "slug": resp.get("slug"), "url": f"https://www.depop.com/products/{resp.get('slug')}/"}

    def get(self, sku):
        return self.call("GET", f"/api/v1/products/by-sku/{quote(sku, safe='')}/")[2]

    def delete(self, sku):
        self.call("DELETE", f"/api/v1/products/by-sku/{quote(sku, safe='')}/")
        return {"platform": "depop", "sku": sku, "deleted": True}

    def addresses(self):
        return self.call("GET", "/api/v1/shop/seller-addresses/")[2]


# --------------------------------------------------------------------------- CLI


def parse_targets(value):
    targets = [t.strip().lower() for t in value.split(",") if t.strip()]
    targets = ["ebay", "depop"] if targets in (["both"], ["all"]) else targets
    bad = set(targets) - {"ebay", "depop"}
    if bad or not targets:
        raise ListingError("--to must be ebay, depop, or ebay,depop")
    return targets


def cmd_validate(args):
    targets = parse_targets(args.to)
    report, ok = [], True
    for path in args.files:
        listing = load_listing(path)
        errors, warnings = validate(listing, targets)
        ok = ok and not errors
        report.append({"file": path, "sku": listing.get("sku"), "valid": not errors,
                       "errors": errors, "warnings": warnings})
    emit(report)
    return 0 if ok else 1


def cmd_publish(args):
    targets = parse_targets(args.to)
    listings = []
    for path in args.files:
        listing = load_listing(path)
        errors, _ = validate(listing, targets)
        if errors:
            emit({"file": path, "valid": False, "errors": errors})
            return 1
        listings.append(listing)
    clients = {"ebay": Ebay if "ebay" in targets else None, "depop": Depop if "depop" in targets else None}
    clients = {k: v() for k, v in clients.items() if v}
    results, failed = [], False
    for listing in listings:
        for name, client in clients.items():
            try:
                results.append({"file": listing["_file"], **client.publish(listing, dry_run=args.dry_run)})
            except (ApiError, ListingError) as exc:
                failed = True
                results.append({"file": listing["_file"], "platform": name, "sku": listing["sku"], "error": str(exc)})
    emit(results)
    return 1 if failed else 0


def cmd_ebay(args):
    ebay = Ebay()
    marketplace = getattr(args, "marketplace", "EBAY_US")
    if args.action == "auth-url":
        emit({"environment": ebay.env, "authUrl": ebay.auth_url(),
              "next": "Open the URL, sign in, approve, then copy the 'code' query parameter from the redirect."})
    elif args.action == "exchange-code":
        emit(ebay.exchange_code(args.code))
    elif args.action == "policies":
        emit(ebay.policies(marketplace))
    elif args.action == "create-location":
        emit(ebay.create_location(args.key, args.postal_code, args.country, args.name))
    elif args.action == "suggest-category":
        emit(ebay.suggest_category(args.query, marketplace))
    elif args.action == "category-info":
        emit(ebay.category_info(args.category_id, marketplace))
    elif args.action == "withdraw":
        emit(ebay.withdraw(args.sku, marketplace))
    return 0


def cmd_depop(args):
    if args.action == "pkce":
        verifier = secrets.token_urlsafe(64)[:96]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        emit({"code_verifier": verifier, "code_challenge": challenge, "code_challenge_method": "S256"})
        return 0
    depop = Depop()
    if args.action == "exchange-code":
        emit(depop.exchange_code(args.code, args.code_verifier, args.redirect_uri))
    elif args.action == "addresses":
        emit(depop.addresses())
    elif args.action == "get":
        emit(depop.get(args.sku))
    elif args.action == "delete":
        emit(depop.delete(args.sku))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Publish resale listings to eBay and Depop.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="Check listing JSON files offline")
    p.add_argument("files", nargs="+")
    p.add_argument("--to", default="ebay,depop")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("publish", help="Create or update listings (idempotent per SKU)")
    p.add_argument("files", nargs="+")
    p.add_argument("--to", default="ebay,depop")
    p.add_argument("--dry-run", action="store_true", help="Print API payloads without calling any API")
    p.set_defaults(func=cmd_publish)

    ebay_parser = sub.add_parser("ebay", help="eBay account setup and helpers")
    ebay_parser.set_defaults(func=cmd_ebay)
    ebay = ebay_parser.add_subparsers(dest="action", required=True)
    ebay.add_parser("auth-url")
    e = ebay.add_parser("exchange-code")
    e.add_argument("--code", required=True)
    for name in ("policies", "suggest-category", "category-info", "withdraw", "create-location"):
        e = ebay.add_parser(name)
        e.add_argument("--marketplace", default="EBAY_US")
        if name == "suggest-category":
            e.add_argument("--query", required=True)
        if name == "category-info":
            e.add_argument("--category-id", required=True)
        if name == "withdraw":
            e.add_argument("--sku", required=True)
        if name == "create-location":
            e.add_argument("--key", required=True)
            e.add_argument("--postal-code", required=True)
            e.add_argument("--country", default="US")
            e.add_argument("--name")

    depop_parser = sub.add_parser("depop", help="Depop account setup and helpers")
    depop = depop_parser.add_subparsers(dest="action", required=True)
    depop.add_parser("pkce")
    d = depop.add_parser("exchange-code")
    d.add_argument("--code", required=True)
    d.add_argument("--code-verifier", required=True)
    d.add_argument("--redirect-uri", required=True)
    depop.add_parser("addresses")
    for name in ("get", "delete"):
        depop.add_parser(name).add_argument("--sku", required=True)
    depop_parser.set_defaults(func=cmd_depop)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ApiError, ListingError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
