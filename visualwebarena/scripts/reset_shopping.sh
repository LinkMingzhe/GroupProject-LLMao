#!/usr/bin/env bash
### Performs a full reset of the shopping environment.
### Note: This takes a while (~2 minutes), so it's not recommended to run this too frequently.

set -euo pipefail

CONTAINER_NAME="${CONTAINER_NAME:-shopping}"
SHOPPING_BASE_URL="${SHOPPING_BASE_URL:-http://localhost:7770}"
SHOPPING_BASE_URL_WITH_SLASH="${SHOPPING_BASE_URL%/}/"

docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
docker run --name "${CONTAINER_NAME}" -p 7770:80 -d shopping_final_0712

# Wait for Magento services to start.
sleep 60

docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento setup:store-config:set --base-url="${SHOPPING_BASE_URL%/}"
docker exec "${CONTAINER_NAME}" mysql -u magentouser -pMyPassword magentodb -e "UPDATE core_config_data SET value='${SHOPPING_BASE_URL_WITH_SLASH}' WHERE path = 'web/secure/base_url';"
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento cache:flush

docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalogrule_product
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalogrule_rule
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalogsearch_fulltext
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalog_category_product
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule customer_grid
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule design_config_grid
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule inventory
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalog_product_category
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalog_product_attribute
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule catalog_product_price
docker exec "${CONTAINER_NAME}" /var/www/magento2/bin/magento indexer:set-mode schedule cataloginventory_stock

