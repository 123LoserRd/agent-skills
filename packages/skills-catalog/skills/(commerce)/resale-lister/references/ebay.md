# eBay integration

APIs used: Sell Inventory API, Commerce Media API (photo upload), Commerce Taxonomy API, Sell Metadata API, Sell Account API. All are official REST APIs under a single developer keyset.

## One-time developer setup (user does this)

1. Create a free account at developer.ebay.com → **Application Keys** → create a keyset (Sandbox to test, Production to go live).
2. Keep the **App ID (Client ID)** and **Cert ID (Client Secret)**.
3. **User Tokens** → **Get a Token from eBay via Your Application** → add an eBay Redirect URL. This gives a **RuName** (looks like `Your_Name-YourApp-PRD-abc123-xyz`). Any accept URL works; the user just copies `code` from the redirect.
4. Export locally:

```bash
export EBAY_CLIENT_ID=...        # App ID
export EBAY_CLIENT_SECRET=...    # Cert ID
export EBAY_RU_NAME=...          # RuName, NOT a URL
export EBAY_ENV=production       # or sandbox
```

5. Seller account: opt in to **Business Policies** (Account → Business policies) and create at least one shipping, payment, and return policy.

## Connect the account

```bash
python3 "$LISTER" ebay auth-url            # user opens it and approves
python3 "$LISTER" ebay exchange-code --code 'v%5E1.1%23i...'   # URL-encoded is fine
python3 "$LISTER" ebay policies            # IDs for the listing JSON
python3 "$LISTER" ebay create-location --key home --postal-code 90210 --country US   # if no location
```

Scopes requested: `sell.inventory` and `sell.account`. Each run mints a fresh access token from the stored refresh token. `EBAY_REFRESH_TOKEN` in the environment overrides the store.

## Category, aspects, condition

```bash
python3 "$LISTER" ebay suggest-category --query "vintage nike t-shirt"
python3 "$LISTER" ebay category-info --category-id 15687
```

`category-info` returns required and recommended aspects with sample values, plus the allowed condition IDs. Aspect values should match eBay's suggestions where possible. Custom values are accepted for most aspects.

Condition ID → Inventory API enum: 1000 `NEW`, 1500 `NEW_OTHER`, 1750 `NEW_WITH_DEFECTS`, 2750 `LIKE_NEW`, 3000 `USED_EXCELLENT`, 4000 `USED_VERY_GOOD`, 5000 `USED_GOOD`, 6000 `USED_ACCEPTABLE`, 7000 `FOR_PARTS_OR_NOT_WORKING`. Apparel categories often list 2990 `PRE_OWNED_EXCELLENT` and 3010 `PRE_OWNED_FAIR`. If the mapped value is rejected, set `ebay.condition` to one the category allows.

## What `publish` does

1. Each local photo → `POST apim.ebay.com/commerce/media/v1_beta/image/create_image_from_file` (multipart `image`) → `GET .../image/{id}` → `imageUrl`. HTTPS URLs pass through unchanged. Limit: 50 uploads per 5 seconds.
2. `PUT /sell/inventory/v1/inventory_item/{sku}` with title, description, aspects, imageUrls, condition, quantity (`Content-Language` from the marketplace).
3. `GET /sell/inventory/v1/offer?sku=` → `PUT /offer/{id}` if an offer exists, otherwise `POST /offer` (fixed price, category, policies, location, price).
4. `POST /offer/{id}/publish` → `listingId` → `https://www.ebay.com/itm/{listingId}`.

The steps are idempotent per SKU, so re-running after a fix is safe. `ebay withdraw --sku` ends the listing but keeps the inventory item, so a later `publish` relists it.

## Common errors

- `invalid_grant`: refresh token expired, revoked, or from the other environment. Reconnect.
- `The item specific X is missing`: add `X` to `ebay.aspects`.
- `Invalid value for fulfillmentPolicyId` or `No policy found`: policy IDs are per marketplace. Re-run `policies --marketplace ...`.
- `Location ... not found`: `merchantLocationKey` doesn't exist. Run `create-location`.
- Sandbox listings appear at sandbox.ebay.com only, and sandbox needs its own keyset and sandbox test user.
