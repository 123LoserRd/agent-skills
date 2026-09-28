# Depop integration

API used: the **Depop Selling API** (`https://partnerapi.depop.com`, staging `https://partnerapi-staging.depop.com`), Depop's official API for automated listing.

## Access

The Selling API is for **approved partners** only (sellers and platforms that apply through partnerapi.depop.com). There is no public self-serve key. If the user isn't approved:

- Tell them clearly, and point them to the partner application.
- Offer to produce the Depop-ready text (the description with the title line and hashtags, category, condition, price) for them to paste into the app.
- Do **not** automate the Depop website or mobile app, and don't use unofficial private endpoints. That breaks Depop's terms and can get the shop banned.

## Credentials

Pick one:

```bash
export DEPOP_ACCESS_TOKEN=...                 # simplest; short-lived
# or, for automatic refresh:
export DEPOP_CLIENT_ID=... DEPOP_CLIENT_SECRET=... DEPOP_REFRESH_TOKEN=...
export DEPOP_ENV=production                   # or staging
```

Refresh tokens are **single-use**. Every refresh returns a new one, and the script writes it to the token store right away and prefers it over `DEPOP_REFRESH_TOKEN`. Access tokens are cached until they expire.

To connect through the OAuth authorization-code flow (PKCE S256 is mandatory):

```bash
python3 "$LISTER" depop pkce          # prints code_verifier + code_challenge
# user opens the partner authorize URL from their Depop onboarding with the code_challenge, approves
python3 "$LISTER" depop exchange-code --code '...' --code-verifier '...' --redirect-uri 'https://...'
```

Required scope: `products_write`, plus `offers_write` if you set `auto_send_offer_price` or `auto_negotiate_offer_price`.

## Photos

Depop only accepts **publicly reachable** JPG, PNG, or WebP URLs (1–8, the first is the cover). There's no upload endpoint. Options:

- The user uploads the photos somewhere public (S3, Cloudflare R2, Cloudinary, their own site) and sets `depop.photoBaseUrl`. The script sends `photoBaseUrl/<file name>` for each local photo.
- Or list full HTTPS URLs in `photos`.

Check that each URL opens in a browser before publishing. A broken URL causes a 422.

## Product fields

`PUT /api/v1/products/by-sku/{sku}/` creates the listing, or updates it if the SKU exists. The listing is live immediately.

Required fields: `description`, `price_amount`, `price_currency`, `quantity`, `pictures`, `department`, `product_type`, `condition`, `brand_name`, `attributes`, `address.country_code`, plus exactly one of `national_shipping_cost` / `depop_shipping`. `size_set_id` and `size_id` are required for product types that have sizes. Get them from the Taxonomy section of the Depop partner docs.

Conditions: `brand_new`, `used_like_new`, `used_excellent`, `used_good`, `used_fair`.

For Depop-managed shipping, `depop addresses` lists seller address IDs. Shipping providers per address come from `/api/v1/shop/seller-addresses/{address_id}/shipping-providers/`.

## Other commands

```bash
python3 "$LISTER" depop get --sku NIKE-TEE-90S-M-001
python3 "$LISTER" depop delete --sku NIKE-TEE-90S-M-001      # confirm with the user first
```

## Common errors

- 401: token expired or invalid. Refresh or reconnect.
- 403: token lacks `products_write`, or the account isn't enabled for the Selling API.
- 422: field validation. The response lists the fields, usually size IDs, `product_type`, price below 1.00, or unreachable pictures.
