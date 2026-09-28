---
name: resale-lister
description: Connects to a seller's eBay and Depop accounts and creates, updates, or removes listings with photos, title, description, price, condition, and category through the official eBay Sell APIs and Depop Selling API. Drafts listing copy from item photos, then uploads and publishes (cross-listing to both marketplaces in one step). Use when the user says "list this on eBay", "post this to Depop", "cross-list my items", "create listings from these photos", "connect my eBay account", "write a title and description for my listing", or "take my listing down". Do NOT use for Shopify, Etsy, Poshmark, Vinted, or Mercari stores, for buying or price-scraping on marketplaces, for order fulfillment and shipping labels, or for building a custom marketplace app from scratch.
metadata:
  version: 1.0.0
  author: github.com/123LoserRd
---

# Resale Lister (eBay + Depop)

Turn item photos into live listings on eBay and Depop. The agent drafts the copy, the bundled script does every API call.

## Setup

```bash
export AGENT_SKILLS_HOME="${AGENT_SKILLS_HOME:-$HOME/.agent-skills}"
export LISTER="$AGENT_SKILLS_HOME/skills/resale-lister/scripts/listing.py"   # adjust if installed elsewhere
```

Python 3.9+, standard library only. Credentials come from environment variables; OAuth tokens are kept in `~/.config/resale-lister/tokens.json` (mode 0600, override with `RESALE_LISTER_HOME`).

**Never ask the user to paste client secrets, refresh tokens, or access tokens into chat.** Ask them to export the variables in their own shell and confirm when done. The one exception is the short-lived OAuth `code` from the consent redirect, which the user may paste.

### Connect eBay (once)

Read `references/ebay.md` for the full walkthrough. Short version:

1. User exports `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET`, `EBAY_RU_NAME` (from developer.ebay.com; `EBAY_ENV=sandbox` to test).
2. `python3 "$LISTER" ebay auth-url` → user opens the URL, signs in, approves, copies `code` from the redirect.
3. `python3 "$LISTER" ebay exchange-code --code '<code>'` → refresh token saved (valid about 18 months).
4. `python3 "$LISTER" ebay policies` → shipping/payment/return policy IDs and inventory locations. No location yet → `ebay create-location --key home --postal-code 90210 --country US`.

### Connect Depop (once)

The Depop Selling API is only for **approved partners** (apply at partnerapi.depop.com). If the user has no partner credentials, say so plainly: Depop can't be automated through its official API without them. Offer to draft the Depop listing text for them to paste into the app instead. Don't suggest scraping or automating the Depop web or mobile app. Details in `references/depop.md`.

With access: user exports `DEPOP_CLIENT_ID` + `DEPOP_CLIENT_SECRET` + `DEPOP_REFRESH_TOKEN`, or just `DEPOP_ACCESS_TOKEN`.

## Workflow

### 1. Gather the item

- Get the photo paths (or a folder). **Look at every photo**: identify brand, item type, colour, material, size tag, era, and visible flaws.
- Ask only for what the photos can't show: price, measurements if they matter, which marketplaces to use. Default is both.
- Photo order matters: front/cover first, then back, tag/label, details, flaws last. Up to 8 for Depop and 24 for eBay.

### 2. Draft the listing

Write one JSON file per item, starting from `templates/listing.example.json`. The schema is in `references/listing-schema.md`.

- **title** (≤ 80 chars, eBay): Brand + item type + key details + size + colour + era/style. No filler ("L@@K", "WOW"), no ALL CAPS.
- **description**: first line is a plain summary. Then size and measurements, material, an honest condition note that names each flaw and which photo shows it, then shipping. On Depop the title is added as the first line and the description can end with 3–5 hashtags.
- **condition**: `new` | `new_other` | `like_new` | `used_excellent` | `used_good` | `used_fair`. Choose conservatively.
- **sku**: unique and stable, for example `BRAND-ITEM-SIZE-###`. Re-publishing the same SKU **updates** the listing, it doesn't duplicate it.
- eBay: find the category with `ebay suggest-category --query "<item>"`, then run `ebay category-info --category-id <id>` and fill **every required aspect** (Size, Color, Department, and so on).
- Depop: set `department`, `product_type`, `address.country_code`, and one shipping option. Photos must be public HTTPS URLs; see `references/depop.md`.

### 3. Validate and preview

```bash
python3 "$LISTER" validate item.json --to ebay,depop
python3 "$LISTER" publish item.json --to ebay,depop --dry-run
```

Fix every error. Show the user the title, description, price, condition, category, and photo order. **Get explicit approval before publishing**, because a publish creates a live, buyable listing.

### 4. Publish

```bash
python3 "$LISTER" publish item.json --to ebay,depop     # many files allowed: publish items/*.json
```

What the script does:

- eBay: uploads local photos to eBay Picture Services → upserts the inventory item → creates or updates the offer → publishes.
- Depop: PUT by SKU (creates or updates).

Report each listing URL back to the user. If one platform fails, the other one's result still stands. Fix the failing one and re-run it with `--to <that platform>`.

### 5. Maintain

- Change price, text, or quantity: edit the JSON and run `publish` again.
- Sold elsewhere or taking it down: `ebay withdraw --sku X` and/or `depop delete --sku X`. **Confirm with the user first.** When an item sells on one platform, offer to take it down on the other.

## Errors

| Symptom                             | Fix                                                                                                   |
| ----------------------------------- | ----------------------------------------------------------------------------------------------------- |
| `Missing environment variable ...`  | User exports it locally; re-run                                                                       |
| eBay `invalid_grant`                | Refresh token expired or revoked → redo `auth-url` / `exchange-code`                                  |
| eBay "item specific ... is missing" | Run `category-info`, add the required aspect to `ebay.aspects`                                        |
| eBay condition not allowed          | Use a `conditionId` from `category-info`, set `ebay.condition` to its enum (see `references/ebay.md`) |
| eBay business policies not found    | Seller must opt in to business policies in eBay account settings, then create them                    |
| Depop 401/403                       | Token expired or missing scope `products_write`; re-auth                                              |
| Depop 422                           | Read the field errors: usually `size_set_id`/`size_id`, `product_type`, or an unreachable photo URL   |

## Example

User: "List this jacket on both, $60" + 5 photos

1. From the photos: Carhartt Detroit jacket, brown duck canvas, size L tag, fraying on the cuffs.
2. Write `carhartt-detroit-l.json`: title "Carhartt Detroit Jacket Brown Duck Canvas Blanket Lined Mens Large Workwear", condition `used_good`, cuff wear called out in the description.
3. `suggest-category` → 57988 (Men's Coats & Jackets). `category-info` → fill Size, Color, Department, Type, Outer Shell Material.
4. Run `validate` and `--dry-run`, show the summary, get "yes".
5. `publish` → `https://www.ebay.com/itm/…` and `https://www.depop.com/products/…`.
