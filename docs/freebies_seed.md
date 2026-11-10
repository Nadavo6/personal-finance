# Freebies seed list

Researched 2026-11-10. **Verify every amount, deadline and eligibility rule on the official source before relying on it.** Reserve-duty rules changed repeatedly in 2025–26 (the IDF moved to "activity tiers" / מדרגי פעילות). The reserve-duty rows come from news and Kol Zchut summaries, not from gov.il. Work-perk rows are **typical patterns, not researched facts**: check the employer's HR portal and the provider's terms.

Suggested columns for the app: `name, category, value_ils, source, received_date, expires, usage_rule, status, link`.

## Reserve duty (מילואים)

| Name | Typical value | How received | Expiry / usage rules | Link |
|---|---|---|---|---|
| Fighter card (כרטיס פייטר, Cal) | ₪30 per reserve day up to day 30, ₪80 per day from day 31, annual cap ₪5,000. 2025 rule: at least 10 days of Order 8 service in a qualifying combat unit | Digital prepaid card, issued after eligibility is confirmed. Covers the reservist and spouse | Spend at restaurants, cafes, shows, attractions, clothing, and government fees (driver's licence, ID). 2026 load: one source says redeem by 31 Dec 2028, another says end of 2026. **Conflict, check the card terms** | [bizportal](https://www.bizportal.co.il/financialconsumerism/news/article/20022577), [Makor Rishon](https://www.makorrishon.co.il/news/economy/article/361616) |
| Vacation card (כרטיס נופש) | Amount set by service days; the 2025 wave covered those with 60+ Order 8 days | Digital reloadable card sent to the phone | Hotels, guesthouses, hostels. Reported redeem-by date: 31 Dec 2027 | [mako](https://www.mako.co.il/news-money/2025_q4/Article-9614dfd0d63c991027.htm) |
| Household economy grant (מענק כלכלת הבית) | 2026 tiers reported: ₪1,250 (tier A+), ₪1,065 (A), ₪815 (B) | Paid after the service threshold is met | Meant for household bills (arnona, electricity). Condition reported: 45+ reserve days in 2026 across all order types | [Kol Zchut](https://www.kolzchut.org.il/he/מענק_כלכלת_הבית_למשרתי_מילואים_פעילים) |
| Arnona discount | Up to 5% for an active-reservist certificate; up to 25% for commanders | Apply to the local authority with the certificate | Not always applied automatically, so check the bill. Per-municipality rules | [ice](https://www.ice.co.il/consumerism/news/article/1065520) |
| Commanders' grant (מענק מפקדים) | About ₪1,452 per year (2026). Officer-grade grants of ₪5,000–20,000 were reported but not yet paid | Via the IDF / Ministry of Defense | Reported delayed to Sep 2026; the 54+ service-days condition is from the source | [mako](https://www.mako.co.il/news-military/2026_q1/Article-536d6736f0ecb91026.htm) |
| Tax credit points (נקודות זיכוי) | Reported: up to about ₪11,616 per year for fighters with 30+ days in the prior year. **Unverified, single source** | Reduces monthly payroll tax | Update the Form 101 with the employer. Check the amount with the payroll department | [bizportal](https://www.bizportal.co.il/career/news/article/20037794) |
| Aid fund grants (קרן הסיוע) | Not researched. I found no confirmed 2026 terms | | Check the reservist hotline: 1111 ext. 4 (per the same news coverage) | |
| Eligibility lookup | n/a | Reservist hotline 1111 ext. 4; gov.il / Mofet | Use it to confirm tier, days counted and entitlements | |

## Work perks (typical for Israeli tech companies, not verified)

| Name | Typical value | How received | Expiry / usage rules | Link |
|---|---|---|---|---|
| Cibus / 10bis meal credit | Monthly employer credit (often a few hundred ₪) | Employer loads it each month onto the app or card | Usually food and restaurants only; unused balance often **does not roll over**. Confirm with your employer | [Cibus](https://www.mysodexo.co.il), [10bis](https://www.10bis.co.il) |
| Holiday gift card (BuyMe etc.) | Often ₪300–1,000 at Rosh Hashana and Pesach | Emailed or SMS'd code, or a physical card | Gift cards usually carry a validity period (commonly years) and may be single-use. **Check the terms on the card.** Employer gifts have a tax-exempt ceiling set by the Tax Authority. I did not find the 2026 figure | [BuyMe](https://buyme.co.il) |
| Wellness / gym budget | Annual or monthly sum, set by the employer | Reimbursement against a receipt, or via a benefits platform | Usually use-it-or-lose-it per calendar year; claim deadlines vary | n/a |
| Learning / conference budget | Annual sum, often a few thousand ₪ | Reimbursement, or the company pays the vendor | Per calendar year; may need manager approval | n/a |
| Birthday / event gifts, company vouchers | Small amounts | Email or the HR portal | Watch expiry dates in the email | n/a |

## Tracking notes

- Record `expires` and send a reminder 30 days before it. Several reserve-duty items have reload cycles per year of service.
- Mark the source confidence per row (`official`, `news`, `employer`). Tag anything marked unverified above as `news` or `employer`, not `official`.
- Fighter-card and vacation-card spend appears on the card statement; tag those merchants so freebie usage can be matched to transactions.
