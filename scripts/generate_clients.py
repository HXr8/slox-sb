#!/usr/bin/env python3
"""
slox_sb - Synthetic Client Generator
Generates 15,000 UHNW client profiles into SQLite.
Free data only: Faker + custom financial generators.
Output: /srv/slox_sb/data/client_profiles/clients.db
"""

import sqlite3, json, random, uuid, math
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42069)  # Reproducible

DB_PATH = Path("/srv/slox_sb/data/client_profiles/clients.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ── AUM Tiers ──────────────────────────────────────────────────────
TOTAL = 15000
TIERS = [
    ("$5M-$10M",    5_000_000,  10_000_000,   4500),
    ("$10M-$30M",   10_000_000, 30_000_000,   4000),
    ("$30M-$75M",   30_000_000, 75_000_000,   3000),
    ("$75M-$150M",  75_000_000, 150_000_000,  1500),
    ("$100M-$300M", 100_000_000,300_000_000,  1000),
    ("$300M-$600M", 300_000_000,600_000_000,  500),
    ("$600M-$1B",   600_000_000,1_000_000_000,250),
    ("$1B-$3B",     1_000_000_000, 3_000_000_000, 150),
    ("$3B+",        3_000_000_000, 20_000_000_000, 100),
]

# ── Nationalities ──────────────────────────────────────────────────
# 50 major nationalities covering ~95% of HNW distribution
NATIONALITIES = [
    ("USA", 0.25), ("China", 0.12), ("Japan", 0.08), ("Germany", 0.06),
    ("UK", 0.05), ("France", 0.04), ("Switzerland", 0.035), ("India", 0.03),
    ("Canada", 0.03), ("Australia", 0.025), ("Italy", 0.02), ("Singapore", 0.02),
    ("Hong Kong", 0.02), ("Taiwan", 0.02), ("UAE", 0.015), ("Saudi Arabia", 0.015),
    ("Brazil", 0.015), ("Mexico", 0.012), ("South Korea", 0.012),
    ("Netherlands", 0.01), ("Sweden", 0.01), ("Norway", 0.01), ("Denmark", 0.01),
    ("Spain", 0.01), ("Russia", 0.015), ("Israel", 0.01), ("Luxembourg", 0.005),
    ("Monaco", 0.005), ("Kuwait", 0.008), ("Qatar", 0.008), ("Bahrain", 0.004),
    ("New Zealand", 0.008), ("Ireland", 0.008), ("Austria", 0.006),
    ("Belgium", 0.006), ("Finland", 0.005), ("Portugal", 0.005), ("Greece", 0.004),
    ("Turkey", 0.008), ("Indonesia", 0.008), ("Thailand", 0.006),
    ("Malaysia", 0.005), ("Philippines", 0.004), ("Vietnam", 0.003),
    ("South Africa", 0.005), ("Nigeria", 0.004), ("Egypt", 0.003),
    ("Argentina", 0.003), ("Chile", 0.003), ("Colombia", 0.003),
]
# Normalise to sum = 0.95 (5% reserved for "Other")
total_w = sum(w for _, w in NATIONALITIES)
NATIONALITIES = [(n, w/total_w * 0.95) for n, w in NATIONALITIES]
NATIONALITIES.append(("Other", 0.05))

# ── Archetypes ─────────────────────────────────────────────────────
ARCHETYPES = [
    ("first_gen_entrepreneur", 0.10,
     "Business owner who sold or took company public. Concentrated wealth from single exit event.",
     lambda tier: {"source": "business_exit", "year": random.randint(2005, 2025),
                   "equity_allocation": random.uniform(0.15, 0.35),
                   "needs_diversification": True}),
    ("multi_gen_family_office", 0.08,
     "Wealth inherited across 2+ generations. Conservative, income-focused.",
     lambda tier: {"source": "inherited", "year": random.randint(1950, 2000),
                   "equity_allocation": random.uniform(0.25, 0.45),
                   "has_governance": tier >= "$150M-$300M"}),
    ("tech_exec_ipo", 0.07,
     "Senior executive at tech company post-IPO. Concentrated single stock, high risk tolerance.",
     lambda tier: {"source": "ipo_windfall", "year": random.randint(2015, 2025),
                   "equity_allocation": random.uniform(0.40, 0.65),
                   "concentrated_stock": True}),
    ("inheritance_receiver", 0.10,
     "Received wealth from parents/spouse. May have lower financial literacy.",
     lambda tier: {"source": "inheritance", "year": random.randint(2010, 2025),
                   "equity_allocation": random.uniform(0.15, 0.30),
                   "needs_education": True}),
    ("athlete_entertainer", 0.04,
     "Professional athlete or entertainer. Short career, lumpy income, post-retirement focus.",
     lambda tier: {"source": "career_earnings", "year": random.randint(2010, 2025),
                   "equity_allocation": random.uniform(0.20, 0.50),
                   "short_horizon": True}),
    ("retired_c_suite", 0.10,
     "Retired executive. Drawdown phase, capital preservation, estate planning priority.",
     lambda tier: {"source": "career_earnings", "year": random.randint(1980, 2010),
                   "equity_allocation": random.uniform(0.20, 0.40),
                   "income_focus": True}),
    ("cross_border_exec", 0.06,
     "Expat executive with multi-jurisdictional exposure. Complex tax and residency planning.",
     lambda tier: {"source": "career_earnings", "year": random.randint(2000, 2025),
                   "equity_allocation": random.uniform(0.30, 0.50),
                   "multi_jurisdiction": True}),
    ("philanthropist", 0.05,
     "Foundation trustee or philanthropist. Mission-driven, tax-efficient giving focus.",
     lambda tier: {"source": "mixed", "year": random.randint(1990, 2020),
                   "equity_allocation": random.uniform(0.30, 0.50),
                   "has_foundation": True}),
    ("pe_vc_partner", 0.05,
     "Private equity or venture capital partner. Illiquid-heavy, co-investment savvy.",
     lambda tier: {"source": "carried_interest", "year": random.randint(2005, 2025),
                   "equity_allocation": random.uniform(0.15, 0.30),
                   "illiquid_heavy": True}),
    ("real_estate_dynast", 0.06,
     "Wealth concentrated in real estate. Low liquidity, succession challenges.",
     lambda tier: {"source": "real_estate", "year": random.randint(1980, 2015),
                   "equity_allocation": random.uniform(0.05, 0.20),
                   "property_heavy": True}),
    ("derivative_sophisticated", 0.04,
     "Active trader using derivatives, hedging programs. Margin efficiency focus.",
     lambda tier: {"source": "trading", "year": random.randint(2010, 2025),
                   "equity_allocation": random.uniform(0.35, 0.60),
                   "uses_derivatives": True}),
    ("insurance_buyer", 0.06,
     "Risk-averse client focused on guaranteed income and longevity hedging.",
     lambda tier: {"source": "mixed", "year": random.randint(1990, 2015),
                   "equity_allocation": random.uniform(0.15, 0.30),
                   "retirement_focused": True}),
    ("credit_user", 0.06,
     "Leverage-heavy client using acquisition finance, margin lending, structured credit.",
     lambda tier: {"source": "business", "year": random.randint(2000, 2025),
                   "equity_allocation": random.uniform(0.25, 0.45),
                   "uses_leverage": True}),
    ("global_citizen", 0.05,
     "Multiple residencies and passports. FATCA/CRS implications. Complex structure needs.",
     lambda tier: {"source": "inherited_and_earned", "year": random.randint(1990, 2020),
                   "equity_allocation": random.uniform(0.25, 0.45),
                   "multi_jurisdiction": True}),
    ("dynasty_founder", 0.04,
     "$1B+ self-made wealth. Building family office. Next-gen education and governance.",
     lambda tier: {"source": "business_empire", "year": random.randint(1980, 2010),
                   "equity_allocation": random.uniform(0.20, 0.40),
                   "building_family_office": True}),
]

# ── First names by nationality rough mapping ───────────────────────
def first_name(nat):
    pools = {
        "USA": ["James","Mary","Robert","Patricia","John","Jennifer","Michael","Linda","David","Barbara","William","Elizabeth","Richard","Susan","Joseph","Jessica","Thomas","Sarah","Christopher","Karen"],
        "China": ["Wei","Li","Jing","Yan","Lei","Xia","Ming","Fang","Hui","Peng","Yu","Tao","Dan","Qiang","Feng","Shu","Long","Juan","Kai","Na"],
        "Japan": ["Haruto","Yuki","Sakura","Ren","Aoi","Yuto","Hinata","Sota","Rin","Kaito","Mio","Itsuki","Mao","Sara","Daiki","Riko","Riku","Miyu","Koharu","Akari"],
        "India": ["Aarav","Vivaan","Aditya","Vihaan","Arjun","Sai","Reyansh","Ayaan","Krishna","Ishaan","Ananya","Diya","Myra","Aanya","Sara","Isha","Kavya","Riya","Nisha","Priya"],
        "Germany": ["Lukas","Marie","Maximilian","Sophie","Leon","Emma","Felix","Mia","Noah","Lena","Elias","Anna","Paul","Lea","Ben","Lilli","Finn","Hannah","Jonas","Amelie"],
        "UK": ["Oliver","Amelia","George","Olivia","Harry","Isla","Jack","Emily","Leo","Ava","Charlie","Ella","Thomas","Harper","James","Sophie","Henry","Grace","William","Lily"],
        "France": ["Lucas","Emma","Gabriel","Louise","Leo","Alice","Raphael","Mila","Louis","Jade","Jules","Lina","Adam","Rose","Arthur","Chloe","Ethan","Mia","Hugo","Zoe"],
        "Switzerland": ["Luca","Sophie","Noah","Emma","Leon","Mia","Elias","Lea","Ben","Sara","Felix","Alina","David","Chiara","Samuel","Lena","Julian","Selina","Nico","Lara"],
        "Singapore": ["Wei Ming","Li Na","Jun Wei","Siti","Rajan","Mei Ling","Kumar","Siew Mei","Ahmad","Hui Min","Raj","Yvonne","Chen Wei","Priya","Derek","Shirley","Adrian","Pei Ling","Kelvin","Sandra"],
        "Hong Kong": ["Ka Wai","Wing Yan","Ho Yan","Man Kit","Ching Wai","Wai Lam","Siu Ming","Ka Yan","Kin Ho","Pui Shan","Chi Hung","Suk Yin","Wai Hung","Ming Yi","Kin Man","Yuk Chun","Kwok Keung","Siu Wai","Wing Sze","Ka Po"],
        "Russia": ["Alexander","Elena","Mikhail","Olga","Dmitry","Natalia","Andrey","Anna","Sergey","Tatiana","Ivan","Marina","Vladimir","Irina","Nikolay","Svetlana","Pavel","Ksenia","Alexey","Daria"],
        "Brazil": ["Lucas","Julia","Gabriel","Maria","Pedro","Ana","Mateus","Camila","Felipe","Isabella","Joao","Leticia","Rafael","Beatriz","Guilherme","Mariana","Gustavo","Victoria","Daniel","Larissa"],
        "Middle East": ["Mohammed","Fatima","Ahmed","Aisha","Ali","Noor","Omar","Layla","Hassan","Mariam","Khalid","Hessa","Saeed","Amna","Abdulla","Mona","Hamad","Hind","Rashid","Shaikha"],
    }
    pool = pools.get(nat, pools["USA"])
    return random.choice(pool)

# ── Last names ─────────────────────────────────────────────────────
LAST_NAMES = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
              "Chen", "Wang", "Li", "Zhang", "Liu", "Yang", "Huang", "Wu", "Zhou", "Xu",
              "Kim", "Park", "Lee", "Choi", "Jung", "Yoon", "Bae", "Seo", "Kang", "Cho",
              "Schmidt", "Mueller", "Weber", "Schneider", "Fischer", "Wagner", "Hoffmann", "Schaefer", "Koch", "Richter",
              "Dupont", "Moreau", "Lefevre", "Martinez", "Bernard", "Petit", "Robert", "Richard", "Durand", "Dubois",
              "Patel", "Shah", "Kumar", "Singh", "Reddy", "Gupta", "Verma", "Joshi", "Desai", "Mehta",
              "Tanaka", "Suzuki", "Takahashi", "Watanabe", "Ito", "Yamamoto", "Nakamura", "Kobayashi", "Sato", "Kato"]

def random_name(nat):
    fn = first_name(nat)
    ln = random.choice(LAST_NAMES)
    return f"{fn} {ln}"

# ── Asset class allocation by archetype ────────────────────────────
def asset_allocation(archetype, tier_value):
    """Return dict of asset class -> percentage."""
    aum = tier_value
    if aum >= 1_000_000_000:
        base = {"public_equity": 0.20, "fixed_income": 0.15, "private_equity": 0.22,
                "private_credit": 0.08, "real_assets": 0.15, "hedge_funds": 0.08,
                "cash": 0.04, "structured_notes": 0.05, "insurance": 0.03}
    elif aum >= 150_000_000:
        base = {"public_equity": 0.28, "fixed_income": 0.20, "private_equity": 0.15,
                "private_credit": 0.06, "real_assets": 0.10, "hedge_funds": 0.06,
                "cash": 0.05, "structured_notes": 0.07, "insurance": 0.03}
    else:
        base = {"public_equity": 0.32, "fixed_income": 0.25, "private_equity": 0.08,
                "private_credit": 0.03, "real_assets": 0.06, "hedge_funds": 0.03,
                "cash": 0.10, "structured_notes": 0.08, "insurance": 0.05}

    # Adjust for archetype
    props = archetype[3](archetype[0])
    if props.get("property_heavy"):
        base["real_assets"] += 0.15
        base["public_equity"] -= 0.10
    if props.get("illiquid_heavy"):
        base["private_equity"] += 0.12
        base["public_equity"] -= 0.08
    if props.get("needs_diversification"):
        base["public_equity"] -= 0.05
        for k in ["fixed_income", "private_equity", "structured_notes"]:
            base[k] += 0.015
    if props.get("income_focus"):
        base["fixed_income"] += 0.10
        base["public_equity"] -= 0.06
        base["private_equity"] -= 0.04
    if props.get("retirement_focused"):
        base["insurance"] += 0.08
        base["public_equity"] -= 0.05
        base["fixed_income"] += 0.05

    # Normalise
    total = sum(base.values())
    return {k: round(v/total, 4) for k, v in base.items()}

# ── Life events timeline ───────────────────────────────────────────
LIFE_EVENTS = ["retirement", "divorce", "inheritance", "business_sale", "child_education",
               "marriage", "relocation", "health_crisis", "philanthropy_commitment", "new_business"]

def generate_life_events():
    """Generate 5-20 scheduled life events over a simulated timeline."""
    n = random.randint(5, 20)
    events = []
    years = sorted(random.sample(range(1, 21), n))
    for y in years[:n]:
        evt = random.choice(LIFE_EVENTS)
        events.append({"year": y, "event": evt, "severity": random.choice(["minor", "moderate", "major"])})
    return events

# ── Generate clients ───────────────────────────────────────────────
def generate_all():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY,
            client_uuid TEXT UNIQUE,
            full_name TEXT,
            age INTEGER,
            nationality TEXT,
            residency TEXT,
            marital_status TEXT,
            dependents INTEGER,
            aum_tier TEXT,
            total_aum REAL,
            archetype TEXT,
            archetype_desc TEXT,
            wealth_source TEXT,
            wealth_year INTEGER,
            asset_allocation TEXT,
            risk_profile TEXT,
            communication_pref TEXT,
            fee_sensitivity TEXT,
            liabilities REAL,
            existing_advisory TEXT,
            life_events TEXT,
            metadata TEXT,
            created_at TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_nationality ON clients(nationality)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_archetype ON clients(archetype)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_aum_tier ON clients(aum_tier)")

    client_id = 0
    for tier_name, aum_min, aum_max, count in TIERS:
        for _ in range(count):
            client_id += 1
            aum = random.uniform(aum_min, aum_max)

            # Pick nationality
            r = random.random()
            cum = 0
            nat = "Other"
            for n, w in NATIONALITIES:
                cum += w
                if r <= cum:
                    nat = n
                    break

            # Pick archetype
            r = random.random()
            cum = 0
            arch = ARCHETYPES[0]
            for a in ARCHETYPES:
                cum += a[1]
                if r <= cum:
                    arch = a
                    break

            name = random_name(nat)
            age = random.randint(28, 85)
            residencies = ["Singapore", "Hong Kong", "Switzerland", "UK", "USA", "UAE",
                          "Monaco", "Luxembourg", "Bahrain", "Cayman Islands"]
            residency = nat if random.random() > 0.3 else random.choice(residencies)
            marital = random.choices(["single", "married", "divorced", "widowed"], weights=[0.15, 0.60, 0.18, 0.07])[0]
            dependents = random.randint(0, 5) if marital != "single" else 0
            risk_profiles = ["conservative", "moderate_conservative", "moderate", "moderate_aggressive", "aggressive"]
            rp_weights = [0.15, 0.20, 0.30, 0.25, 0.10]
            risk_profile = random.choices(risk_profiles, weights=rp_weights)[0]
            comm_pref = random.choice(["monthly_email", "quarterly_meeting", "annual_review", "digital_dashboard"])
            fee_sens = random.choice(["low", "medium", "high"])

            alloc = asset_allocation(arch, aum)
            liabilities = aum * random.uniform(0.0, 0.12) if random.random() > 0.5 else 0
            existing = random.choice(["self_directed", "full_service_broker", "trust_company", "family_office", "none"])

            life_events = generate_life_events()
            arch_props = arch[3](arch[0])

            metadata = {
                "generated_at": datetime.utcnow().isoformat(),
                "version": "1.0",
                "aum_min_tier": aum_min,
                "aum_max_tier": aum_max,
                "simulation_years": 20,
            }

            uuid_str = str(uuid.uuid4())

            cur.execute("""
                INSERT INTO clients
                (id, client_uuid, full_name, age, nationality, residency, marital_status,
                 dependents, aum_tier, total_aum, archetype, archetype_desc,
                 wealth_source, wealth_year, asset_allocation, risk_profile,
                 communication_pref, fee_sensitivity, liabilities, existing_advisory,
                 life_events, metadata, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                client_id, uuid_str, name, age, nat, residency, marital,
                dependents, tier_name, round(aum, 2), arch[0], arch[1],
                arch_props.get("source", "mixed"), arch_props.get("year", 2000),
                json.dumps(alloc), risk_profile,
                comm_pref, fee_sens, round(liabilities, 2), existing,
                json.dumps(life_events), json.dumps(metadata), datetime.utcnow().isoformat()
            ))

            if client_id % 1000 == 0:
                conn.commit()
                print(f"  Generated {client_id}/{TOTAL}...")

    conn.commit()

    # Stats
    cur.execute("SELECT COUNT(*), MIN(total_aum), MAX(total_aum), AVG(total_aum) FROM clients")
    cnt, mn, mx, avg = cur.fetchone()
    print(f"\nTotal clients: {cnt:,}")
    print(f"AUM range: ${mn:,.0f} - ${mx:,.0f}")
    print(f"Average AUM: ${avg:,.0f}")

    cur.execute("SELECT nationality, COUNT(*) FROM clients GROUP BY nationality ORDER BY COUNT(*) DESC LIMIT 10")
    print("\nTop 10 nationalities:")
    for n, c in cur.fetchall():
        print(f"  {n}: {c}")

    cur.execute("SELECT archetype, COUNT(*) FROM clients GROUP BY archetype ORDER BY COUNT(*) DESC")
    print("\nArchetype distribution:")
    for a, c in cur.fetchall():
        print(f"  {a}: {c}")

    conn.close()
    print(f"\nDatabase: {DB_PATH}")
    print(f"Size: {DB_PATH.stat().st_size / 1024 / 1024:.1f} MB")

if __name__ == "__main__":
    print(f"Generating {TOTAL:,} synthetic UHNW client profiles...")
    generate_all()
