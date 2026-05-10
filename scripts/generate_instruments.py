#!/usr/bin/env python3
"""
slox_sb - Synthetic Product Libraries Generator
Generates 5K+ public securities, 200 structured products, 100 PE funds,
50 credit termsheets, 50 insurance policies.
Output: /srv/slox_sb/data/instruments/*.json
"""

import json, random, math
from pathlib import Path

OUT = Path("/srv/slox_sb/data/instruments")
OUT.mkdir(parents=True, exist_ok=True)
random.seed(12345)

# ── 5,000+ Public Securities ───────────────────────────────────────
SECTORS = ["Technology", "Healthcare", "Financials", "Consumer Discretionary",
           "Consumer Staples", "Energy", "Industrials", "Materials",
           "Utilities", "Real Estate", "Communication Services"]
EXCHANGES = ["NYSE", "NASDAQ", "LSE", "TSE", "HKEX", "SGX", "SWX", "FWB"]

def gen_public_securities(n=5200):
    securities = []
    for i in range(n):
        ticker = f"{chr(65 + (i % 26))}{chr(65 + ((i // 26) % 26))}{chr(65 + ((i // 676) % 26))}"
        sec = random.choice(SECTORS)
        exch = random.choice(EXCHANGES)
        cap = random.choice(["large", "mid", "small"])
        vol = {"large": random.uniform(0.12, 0.35), "mid": random.uniform(0.20, 0.50),
               "small": random.uniform(0.30, 0.80)}[cap]
        div = random.uniform(0, 0.05) if cap != "small" else random.uniform(0, 0.02)
        beta = random.gauss(1.0, 0.4)
        securities.append({
            "ticker": ticker, "name": f"{sec} Corp {i}",
            "sector": sec, "exchange": exch, "cap_tier": cap,
            "volatility": round(vol, 4), "div_yield": round(div, 4),
            "beta": round(beta, 2), "avg_volume": random.randint(100000, 50000000),
            "market_cap": round(random.uniform(0.5e9, 500e9), 0)
        })
    (OUT / "public_securities.json").write_text(json.dumps(securities, indent=2))
    print(f"Public securities: {len(securities)} generated")

# ── 200 Structured Products ────────────────────────────────────────
STRUCTURED_TYPES = ["Principal-Protected Note", "Autocallable Note",
                    "Reverse Convertible", "Growth Note", "Yield Enhancement Note",
                    "Range Accrual Note", "Dual Currency Note", "Fixed Coupon Note"]

def gen_structured_products(n=200):
    products = []
    for i in range(n):
        typ = random.choice(STRUCTURED_TYPES)
        tenor = random.choice([1, 2, 3, 5, 7])
        coupon = round(random.uniform(0.03, 0.12), 4)
        barrier = round(random.uniform(0.5, 0.8), 2) if "reverse" in typ.lower() or "autocall" in typ.lower() else None
        issuer = random.choice(["GS", "JPM", "MS", "UBS", "CS", "BAML", "CITI", "DB", "BNP", "SG"])
        products.append({
            "id": f"ST{i:04d}", "type": typ, "tenor_years": tenor,
            "coupon": coupon, "barrier": barrier,
            "underlying_count": random.randint(1, 5),
            "currency": random.choice(["USD", "EUR", "CHF", "SGD", "HKD"]),
            "min_investment": random.choice([250000, 500000, 1000000]),
            "issuer": issuer, "listing": "OTC",
            "early_redemption": random.random() > 0.3,
        })
    (OUT / "structured_products.json").write_text(json.dumps(products, indent=2))
    print(f"Structured products: {len(products)} generated")

# ── 100 Private Asset Funds ────────────────────────────────────────
PE_TYPES = ["Buyout", "Venture Capital", "Growth Equity", "Private Credit",
            "Real Estate", "Infrastructure", "Natural Resources", "Secondaries"]

def gen_private_assets(n=100):
    funds = []
    for i in range(n):
        typ = random.choice(PE_TYPES)
        vintage = random.randint(2005, 2025)
        fund_size = round(random.uniform(50e6, 15e9), 0)
        irr = {"Buyout": random.uniform(0.08, 0.22),
               "Venture Capital": random.uniform(-0.05, 0.35),
               "Growth Equity": random.uniform(0.06, 0.25),
               "Private Credit": random.uniform(0.06, 0.14),
               "Real Estate": random.uniform(0.05, 0.15),
               "Infrastructure": random.uniform(0.06, 0.14),
               "Natural Resources": random.uniform(0.04, 0.20),
               "Secondaries": random.uniform(0.08, 0.18)}[typ]
        funds.append({
            "id": f"PE{i:04d}", "name": f"{typ} Fund {vintage}-{i}",
            "type": typ, "vintage": vintage, "fund_size": fund_size,
            "net_irr": round(irr, 4), "lockup_years": random.choice([5, 7, 10]),
            "management_fee": round(random.uniform(0.01, 0.02), 3),
            "carry": round(random.uniform(0.15, 0.25), 2),
            "currency": "USD", "status": random.choice(["open", "closed", "fully_invested"])
        })
    (OUT / "private_assets.json").write_text(json.dumps(funds, indent=2))
    print(f"Private asset funds: {len(funds)} generated")

# ── 50 Credit Terms Sheets ─────────────────────────────────────────
CREDIT_TYPES = ["SBL", "Residential Mortgage", "Acquisition Finance", "Trade Finance",
                "Art-Secured", "PE Commitment Financing", "Bridge Loan", "Construction Loan"]

def gen_credit_termsheets(n=50):
    sheets = []
    for i in range(n):
        typ = random.choice(CREDIT_TYPES)
        ltv = {"SBL": random.uniform(0.50, 0.80),
               "Residential Mortgage": random.uniform(0.50, 0.75),
               "Acquisition Finance": random.uniform(0.40, 0.70),
               "Trade Finance": random.uniform(0.60, 0.85),
               "Art-Secured": random.uniform(0.30, 0.55),
               "PE Commitment Financing": random.uniform(0.60, 0.80),
               "Bridge Loan": random.uniform(0.50, 0.70),
               "Construction Loan": random.uniform(0.55, 0.75)}[typ]
        spread = round(random.uniform(0.02, 0.08), 4)
        tenor = random.choice([1, 2, 3, 5, 7, 10])
        sheets.append({
            "id": f"CR{i:04d}", "type": typ, "ltv": round(ltv, 2),
            "spread": spread, "tenor_years": tenor,
            "currency": random.choice(["USD", "EUR", "CHF", "SGD"]),
            "min_loan": round(random.uniform(500000, 50000000), 0),
            "interest_type": random.choice(["fixed", "floating"]),
            "covenants": random.choice(["none", "financial", "both"]),
            "prepayment_penalty": random.choice([0, 0.01, 0.02, 0.03]),
        })
    (OUT / "credit_termsheets.json").write_text(json.dumps(sheets, indent=2))
    print(f"Credit termsheets: {len(sheets)} generated")

# ── 50 Insurance Policies ──────────────────────────────────────────
INS_TYPES = ["Whole Life", "Term Life", "Universal Life", "Variable Universal Life",
             "Fixed Annuity", "Variable Annuity", "Indexed Annuity",
             "Long-Term Care", "Key Person Insurance", "Offshore Life Wrapper"]

def gen_insurance(n=50):
    policies = []
    for i in range(n):
        typ = random.choice(INS_TYPES)
        min_premium = {"Whole Life": 50000, "Term Life": 10000, "Universal Life": 25000,
                       "Variable Universal Life": 25000, "Fixed Annuity": 100000,
                       "Variable Annuity": 50000, "Indexed Annuity": 50000,
                       "Long-Term Care": 10000, "Key Person Insurance": 500000,
                       "Offshore Life Wrapper": 1000000}[typ]
        policies.append({
            "id": f"IN{i:04d}", "type": typ,
            "guaranteed_min_return": round(random.uniform(0.01, 0.04), 3) if "annuity" in typ.lower() else 0,
            "projected_return": round(random.uniform(0.03, 0.08), 3),
            "min_premium": min_premium, "currency": "USD",
            "surrender_schedule_years": random.choice([5, 7, 10, 15]),
            "carrier_rating": random.choice(["AA+", "AA", "AA-", "A+", "A", "A-"]),
        })
    (OUT / "insurance_policies.json").write_text(json.dumps(policies, indent=2))
    print(f"Insurance policies: {len(policies)} generated")

# ── Run all ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    gen_public_securities()
    gen_structured_products()
    gen_private_assets()
    gen_credit_termsheets()
    gen_insurance()
    print("\nAll instruments generated.")
    total = sum(1 for f in OUT.glob("*.json"))
    total_kb = sum(f.stat().st_size for f in OUT.glob("*.json")) / 1024
    print(f"Files: {total} | Total size: {total_kb:.0f} KB")
