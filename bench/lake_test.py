import duckdb
con = duckdb.connect()
con.execute("INSTALL ducklake; LOAD ducklake;")
con.execute("ATTACH 'ducklake:meta.ducklake' AS lake (DATA_PATH 'data/')")
con.execute("USE lake")
con.execute("CREATE TABLE edge (input_id VARCHAR, output_id VARCHAR, amount DOUBLE)")
con.execute("INSERT INTO edge VALUES ('fabric','tshirt',0.25), ('elec','tshirt',0.5)")
con.execute("UPDATE edge SET amount = 0.30 WHERE input_id = 'fabric'")
print("snapshots:", con.execute("SELECT snapshot_id, changes FROM lake.snapshots() ORDER BY snapshot_id").fetchall())
v = con.execute("SELECT max(snapshot_id) FROM lake.snapshots()").fetchone()[0]
print("now:", con.execute("SELECT * FROM edge ORDER BY input_id").fetchall())
print("as of previous snapshot:", con.execute(f"SELECT * FROM edge AT (VERSION => {v-1}) ORDER BY input_id").fetchall())
print("changes:", con.execute(f"SELECT * FROM lake.table_changes('edge', {v-1}, {v})").fetchall())
print("ducklake ext version:", con.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='ducklake'").fetchone())
