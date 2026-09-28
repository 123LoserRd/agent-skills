# Listing JSON schema

One file per item. Relative photo paths resolve against the JSON file's folder.

## Shared fields

| Field         | Type        | Required    | Notes                                                                        |
| ------------- | ----------- | ----------- | ---------------------------------------------------------------------------- |
| `sku`         | string ≤ 50 | yes         | Unique, stable, never reused. Re-publishing the same SKU updates the listing |
| `title`       | string ≤ 80 | yes         | eBay title. On Depop it's added as the description's first line              |
| `description` | string      | yes         | Plain text. Line breaks are kept                                             |
| `price`       | string      | yes         | `"35.00"`. Minimum 1.00                                                      |
| `currency`    | string      | yes         | ISO code: `USD`, `GBP`, `EUR`, `AUD`                                         |
| `quantity`    | int         | no          | Default 1                                                                    |
| `condition`   | enum        | yes         | See the mapping below                                                        |
| `brand`       | string      | recommended | Fills the eBay `Brand` aspect and Depop `brand_name` (`unbranded` if empty)  |
| `photos`      | string[]    | yes         | Local paths or HTTPS URLs. The first one is the cover                        |

## Condition mapping

| canonical        | eBay `condition`               | Depop `condition` |
| ---------------- | ------------------------------ | ----------------- |
| `new`            | `NEW` (new with tags)          | `brand_new`       |
| `new_other`      | `NEW_OTHER` (new without tags) | `used_like_new`   |
| `like_new`       | `LIKE_NEW`                     | `used_like_new`   |
| `used_excellent` | `USED_EXCELLENT`               | `used_excellent`  |
| `used_good`      | `USED_GOOD`                    | `used_good`       |
| `used_fair`      | `USED_ACCEPTABLE`              | `used_fair`       |

Override a platform's value with `ebay.condition` or `depop.condition`.

## `ebay` block

| Field                                                      | Required      | Source                                                            |
| ---------------------------------------------------------- | ------------- | ----------------------------------------------------------------- |
| `categoryId`                                               | yes           | `ebay suggest-category --query ...`                               |
| `merchantLocationKey`                                      | yes           | `ebay policies` → `locations`, or `ebay create-location`          |
| `fulfillmentPolicyId`, `paymentPolicyId`, `returnPolicyId` | yes           | `ebay policies`                                                   |
| `aspects`                                                  | required ones | `ebay category-info --category-id ...`. Object of name → string[] |
| `marketplaceId`                                            | no            | Default `EBAY_US` (`EBAY_GB`, `EBAY_DE`, `EBAY_AU`, ...)          |
| `conditionDescription`                                     | no            | Flaw notes. Ignored for NEW* conditions                           |
| `condition`                                                | no            | Raw eBay ConditionEnum override                                   |
| `bestOffer`                                                | no            | `true` enables Best Offer                                         |
| `contentLanguage`                                          | no            | Defaults from marketplace (`en-US`, ...)                          |

## `depop` block

Every key except `photoBaseUrl` is sent to Depop unchanged, so any Selling API product field works here (`discount_price`, `auto_send_offer_price`, `international_shipping_cost`, `is_boosted`, ...).

| Field                                            | Required                        | Notes                                                                                                             |
| ------------------------------------------------ | ------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `address.country_code`                           | yes                             | `US`, `GB`, ... (`state` optional)                                                                                |
| `department`                                     | yes                             | `womenswear`, `menswear`, `kidswear`, `everything-else`                                                           |
| `product_type`                                   | yes                             | Depop taxonomy slug, e.g. `tshirts`, `jeans`, `sneakers`                                                          |
| `size_set_id`, `size_id`                         | when the product type has sizes | Depop taxonomy IDs                                                                                                |
| `national_shipping_cost` **or** `depop_shipping` | exactly one                     | String cost (`"0"` = free), or `{address_id, shipping_provider_id, parcel_size_id, payer}` from `depop addresses` |
| `attributes`                                     | no                              | Defaults to `{}`                                                                                                  |
| `colour`, `style`, `age`, `source`               | no                              | String arrays                                                                                                     |
| `photoBaseUrl`                                   | when photos are local           | Public folder URL. Each local file name is appended to it                                                         |
