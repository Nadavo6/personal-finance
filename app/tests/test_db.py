from finapp import config, db, rules
from tests.backend_util import conn  # noqa: F401


def test_schema_and_version(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("accounts", "transactions", "categories", "rules", "goals", "freebies", "imports",
              "settings", "tasks", "insight_dismissals", "manual_insights", "txn_aliases"):
        assert t in tables
    assert db.get_setting(conn, "schema_version") == str(db.SCHEMA_VERSION)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    cols = {r[1] for r in conn.execute("PRAGMA table_info(freebies)")}
    assert "account_id" in cols


def test_migrate_idempotent(conn):
    n = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    db.migrate(conn)
    db.migrate(conn)
    assert conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == n


def test_db_path_override(conn, tmp_path):
    assert config.db_path() == tmp_path / "t.db"
    assert (tmp_path / "t.db").exists()


def test_default_settings(conn):
    s = db.get_settings(conn)
    assert s["cashflow_start_day"] == "1"
    assert s["buffer_months"] == "1"
    assert s["overspend_pct"] == "120"
    assert s["sync_providers"] == "leumi,max"


def test_accounts_kind_allows_gift(conn):
    conn.execute("INSERT INTO accounts(key,provider,kind,name) VALUES ('cal:1','cal','gift','g')")


def test_seed_categories(conn):
    rows = {r["name"]: dict(r) for r in conn.execute("SELECT * FROM categories")}
    for name, kind in [("Reserve duty income", "income"), ("Mortgage", "fixed"),
                       ("Arnona", "fixed"), ("Water", "fixed"), ("Donations", "variable"),
                       ("Sports", "variable"), ("Digital", "fixed"), ("Loan", "fixed"),
                       ("Fees", "fixed"), ("Card payment", "transfer"),
                       ("Internal transfer", "transfer"), ("Savings & investments", "savings"),
                       ("Uncategorized", "tracking"), ("Salary", "income")]:
        assert rows[name]["kind"] == kind, name
        assert rows[name]["name_he"] and rows[name]["color"]
    assert rows["Reserve duty income"]["name_he"] == "מילואים"


def test_every_riseup_category_maps(conn):
    names = ("אוכל בחוץ, אחר, ארנונה, ביטוח, בריאות, דיגיטל, הלוואה, העברות, הפקדת מזומן, "
             "השקעה וחיסכון, חינוך, כללי, מזומן, מילואים, מים, משיכת פקדון, משכורת, משכנתא, "
             "משכנתא הדירה ברחוב הראשי, סופר, ספורט, עמלות, קניות, רכב ודלק, שיק, שכירות ושכר לימוד, "
             "תחבורה ציבורית, תיירות, תקשורת, תרומה, תשלומים, RiseUp").split(", ")
    for n in names:
        assert rules.map_riseup_category(conn, n) is not None, n


def test_normalizer():
    n = db.normalize_merchant
    assert n("מקס איט פיננ-י") == "מקס איט פיננ"
    assert n("‏שופרסל דיל 123‎") == "שופרסל דיל"
    assert n("סניף 45/ קפה  גרג") == "סניף קפה גרג"
    assert n("NETFLIX.COM 866-579") == "netflix com"
    assert n("PAYPAL *SPOTIFY") == "paypal spotify"
    assert n("ארומה - תל אביב 12") == "ארומה תל אביב"
    assert n("") == ""
    assert n(None) == ""


def test_backup_keeps_n(conn, tmp_path):
    d = tmp_path / "bk"
    for i in range(3):
        (d).mkdir(exist_ok=True)
        (d / f"finance-2000010{i}-0000.db").write_bytes(b"x")
    p = db.backup(d, keep=2)
    assert p.exists()
    left = sorted(x.name for x in d.glob("finance-*.db"))
    assert len(left) == 2 and p.name in left
