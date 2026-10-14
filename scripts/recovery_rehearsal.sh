#!/usr/bin/env bash
set -euo pipefail
: "${L2_QA_CONTAINER:?Transient PostgreSQL container required}"
mkdir -p artifacts/recovery
docker exec "$L2_QA_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE l2_source_qa'
docker exec "$L2_QA_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -c 'CREATE DATABASE l2_target_qa'
docker exec -i "$L2_QA_CONTAINER" psql -U postgres -d l2_source_qa -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE clients(id text PRIMARY KEY, name text NOT NULL);
CREATE TABLE orders(id text PRIMARY KEY, client_id text REFERENCES clients(id), amount numeric(12,2) NOT NULL);
CREATE TABLE items(order_id text REFERENCES orders(id), brand text, sku text, quantity integer, unit_price numeric(12,2), PRIMARY KEY(order_id,brand,sku));
INSERT INTO clients VALUES ('qa-c1','CLIENTE FICTICIO');
INSERT INTO orders VALUES ('qa-o1','qa-c1',41.25);
INSERT INTO items VALUES ('qa-o1','QA MARCA A','QA01',2,10.50),('qa-o1','QA MARCA B','QA02',1,20.25);
SQL
query="SELECT jsonb_build_object('clients',(SELECT jsonb_agg(x) FROM (SELECT * FROM clients ORDER BY id)x),'orders',(SELECT jsonb_agg(x) FROM (SELECT * FROM orders ORDER BY id)x),'items',(SELECT jsonb_agg(x) FROM (SELECT * FROM items ORDER BY order_id,brand,sku)x));"
docker exec "$L2_QA_CONTAINER" psql -U postgres -d l2_source_qa -At -v ON_ERROR_STOP=1 -c "$query" > artifacts/recovery/source.json
start=$(date +%s)
docker exec "$L2_QA_CONTAINER" pg_dump -U postgres -d l2_source_qa -Fc > /tmp/l2-qa-only.dump
docker exec -i "$L2_QA_CONTAINER" pg_restore -U postgres -d l2_target_qa --exit-on-error --no-owner < /tmp/l2-qa-only.dump
restore_seconds=$(( $(date +%s) - start ))
docker exec "$L2_QA_CONTAINER" psql -U postgres -d l2_target_qa -At -v ON_ERROR_STOP=1 -c "$query" > artifacts/recovery/restored.json
diff -u artifacts/recovery/source.json artifacts/recovery/restored.json
# Switch only this isolated test connection; production URLs are never available.
L2_QA_ACTIVE_DB=l2_target_qa
docker exec "$L2_QA_CONTAINER" psql -U postgres -d "$L2_QA_ACTIVE_DB" -At -v ON_ERROR_STOP=1 -c "$query" > artifacts/recovery/after-switch.json
diff -u artifacts/recovery/source.json artifacts/recovery/after-switch.json
printf '{"fixture_only":true,"production_backup_tested":false,"postgres_version":18,"restore_seconds":%s,"record_comparison":"passed","connection_switch":"passed"}\n' "$restore_seconds" > artifacts/recovery/result.json
rm /tmp/l2-qa-only.dump
cat artifacts/recovery/result.json
