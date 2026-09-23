# Common ops commands. Run from repo root: `make -f ops/Makefile <target>`

.PHONY: help schema seed rpc smoke build-all enrich-stale dashboard worker-deploy

help:
	@echo "Targets:"
	@echo "  schema          Apply 001_init.sql"
	@echo "  seed            Apply 002_seed.sql (project + segments)"
	@echo "  rpc             Apply 003_rpc.sql"
	@echo "  smoke           Pull 10 golf-cart-rental companies"
	@echo "  build-all       Run the full 2k anchor list build"
	@echo "  enrich-stale    Re-enrich anything older than 30 days"
	@echo "  dashboard       Run Streamlit locally"
	@echo "  worker-deploy   Push tracking worker to Cloudflare"

schema:
	psql "$$DATABASE_URL" -f schema/001_init.sql

seed:
	psql "$$DATABASE_URL" -f schema/002_seed.sql

rpc:
	psql "$$DATABASE_URL" -f schema/003_rpc.sql

bootstrap: schema seed rpc

smoke:
	python -m enrichment.pipeline build-anchor-list \
		--only-segments golf-cart-rental --limit 10

build-all:
	python -m enrichment.pipeline build-anchor-list

enrich-stale:
	python -m enrichment.pipeline enrich-stale --age-days 30

dashboard:
	streamlit run dashboard/streamlit_app.py

worker-deploy:
	cd tracking && wrangler deploy
