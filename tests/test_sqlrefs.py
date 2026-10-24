from pdcat.sqlrefs import SqlRef, extract_refs, looks_like_sql


def refs(sql):
    return set(extract_refs(sql))


def test_reads_and_joins_with_brackets():
    assert refs("SELECT * FROM [dbo].[Sales] s JOIN Ref.Products p ON 1=1") == {
        SqlRef("reads", "dbo.sales"), SqlRef("reads", "ref.products")}


def test_writes_are_not_also_reads():
    assert refs("DELETE FROM dbo.Forecast WHERE run = 1") == {SqlRef("writes", "dbo.forecast")}
    assert refs("INSERT INTO ml.Out (a) SELECT a FROM ml.Stage") == {
        SqlRef("writes", "ml.out"), SqlRef("reads", "ml.stage")}


def test_update_through_alias():
    assert SqlRef("writes", "dbo.forecast") in refs("UPDATE f SET x = 1 FROM dbo.Forecast f")


def test_skips_ctes_temp_tables_variables_and_dynamic_names():
    sql = """
        WITH recent AS (SELECT * FROM dbo.Sales)
        SELECT * INTO #tmp FROM recent JOIN @lookup l ON 1=1 JOIN {table} t ON 1=1
    """
    assert refs(sql) == {SqlRef("reads", "dbo.sales")}


def test_ignores_comments_and_handles_default_schema():
    assert refs("SELECT a FROM Analytics..Sales -- FROM dbo.NotThis") == {SqlRef("reads", "analytics.dbo.sales")}


def test_exec_procedure():
    assert refs("EXEC dbo.usp_Load @d = 1") == {SqlRef("calls", "dbo.usp_load")}


def test_prose_is_not_sql():
    assert not looks_like_sql("pick the best model from the list")
    assert looks_like_sql("select x from y")
