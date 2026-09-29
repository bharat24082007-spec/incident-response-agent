"""Northstar Commerce incident history and fresh demo alerts."""


def incident(id, date, service, alert, error_log, root_cause, fix_that_worked, fixes_that_failed, time_to_resolve_minutes):
    return {
        "id": id,
        "date": date,
        "service": service,
        "alert": alert,
        "error_log": error_log.strip(),
        "root_cause": root_cause,
        "fix_that_worked": fix_that_worked,
        "fixes_that_failed": fixes_that_failed,
        "time_to_resolve_minutes": time_to_resolve_minutes,
    }


INCIDENTS = [
    incident(
        "INC-2025-001", "2025-01-12", "payments-gateway",
        "Payment authorization requests failed as the session Redis connection count reached its limit.",
        """2025-01-12T09:14:02Z payments-gateway: redis endpoint=payments-session-redis.northstar.internal:6379
2025-01-12T09:14:05Z redis_pool: REDIS_POOL_MAX_SIZE=256; REDIS_MAXCLIENTS=12000; replicas=24
2025-01-12T09:14:08Z redis: connected_clients=11998 rejected_connections=317
2025-01-12T09:14:10Z payments-gateway: Error 111 connecting to payments-session-redis:6379; retry budget exhausted""",
        "Release pgw-2025.01.12 leaked pooled connections; 24 replicas at REDIS_POOL_MAX_SIZE=256 could exceed the Redis connection budget.",
        "Rolled back pgw-2025.01.12, set REDIS_POOL_MAX_SIZE=32, enabled the shared connection pool, and drained stale sessions. Payment errors returned to baseline in 34 minutes.",
        "Restarting Redis did not stop the leak. Raising REDIS_MAXCLIENTS above 12000 only delayed another saturation.", 34,
    ),
    incident(
        "INC-2025-002", "2025-02-03", "orders-api",
        "Order writes timed out while the Northstar orders database ran out of client connections.",
        """2025-02-03T18:21:11Z orders-api: pq: sorry, too many clients already
2025-02-03T18:21:14Z pg_stat_activity: host=orders-postgres-primary; active=588; max_connections=600
2025-02-03T18:21:17Z orders-api: DB_POOL_MAX_SIZE=120; replicas=5; idle_in_transaction=271
2025-02-03T18:21:19Z orders-api: context deadline exceeded after 5.0s""",
        "The export worker held idle transactions while five orders-api replicas each allowed DB_POOL_MAX_SIZE=120; aggregate demand exceeded orders-postgres-primary capacity.",
        "Stopped the export worker, terminated its idle sessions, set DB_POOL_MAX_SIZE=32, and enabled PgBouncer transaction pooling with DB_IDLE_TX_TIMEOUT=60s. Writes recovered in 42 minutes.",
        "Increasing max_connections to 900 caused memory pressure. Restarting orders-api left the export worker's database sessions open.", 42,
    ),
    incident(
        "INC-2025-003", "2025-02-19", "fulfillment-worker",
        "The order-events consumer repeatedly rebalanced and fell behind the Northstar event stream.",
        """2025-02-19T06:30:00Z kafka-lag-exporter: topic=northstar.order-events group=fulfillment-writers lag=184203
2025-02-19T06:30:05Z fulfillment-worker: KAFKA_MAX_POLL_INTERVAL_MS=300000; DB_BATCH_WRITE_SIZE=5000
2025-02-19T06:30:08Z WARN CommitFailedException: Offset commit cannot be completed
2025-02-19T06:30:10Z consumer-coordinator: member left group after 318s synchronous write""",
        "A 5000-row synchronous database batch took longer than KAFKA_MAX_POLL_INTERVAL_MS=300000, triggering a consumer rebalance before offset commit.",
        "Reduced DB_BATCH_WRITE_SIZE to 200, added the missing order_events.created_at index, and scaled fulfillment-writers from 6 to 12 consumers within the 24-partition limit. Lag cleared in 51 minutes.",
        "Increasing KAFKA_MAX_POLL_INTERVAL_MS to 900000 hid the timeout but let stuck consumers hold partitions longer. Adding consumers past 24 did not add throughput.", 51,
    ),
    incident(
        "INC-2025-004", "2025-03-07", "checkout-edge",
        "Checkout requests returned 502 after payments-gateway release 4.12 changed its listener port.",
        """2025-03-07T12:01:22Z checkout-edge/nginx: connect() failed (111: Connection refused) while connecting to upstream
2025-03-07T12:01:22Z upstream: http://payments-gateway.northstar.svc:8080/authorize
2025-03-07T12:01:23Z payments-gateway: release=4.12 LISTEN_PORT=8081; targetPort=8081
2025-03-07T12:01:25Z nginx: upstream prematurely closed connection; status=502""",
        "checkout-edge still sent traffic to port 8080 after payments-gateway 4.12 moved LISTEN_PORT and targetPort to 8081.",
        "Set CHECKOUT_PAYMENTS_UPSTREAM_PORT=8081, rolled out the matching checkout-edge config, and waited for payments-gateway readiness before restoring traffic. 502s cleared in 27 minutes.",
        "Increasing PROXY_READ_TIMEOUT_SECONDS did not help because the TCP connection was refused. Restarting Nginx reloaded the same incorrect port 8080.", 27,
    ),
    incident(
        "INC-2025-005", "2025-03-22", "catalog-api",
        "Catalog API pods were OOMKilled as resident memory grew steadily under normal product traffic.",
        """2025-03-22T15:40:01Z catalog-api-7dc9: heap_used=1870MiB rss=2.1GiB
2025-03-22T15:40:04Z kubelet: catalog-api OOMKilled; memory_limit=2GiB
2025-03-22T15:40:06Z catalog-api: CATALOG_CACHE_MAX_ENTRIES=0; CATALOG_CACHE_TTL_SECONDS=0
2025-03-22T15:40:10Z catalog-api: restart_count=6 over 42m""",
        "CATALOG_CACHE_MAX_ENTRIES=0 disabled the cache bound and CATALOG_CACHE_TTL_SECONDS=0 disabled expiry, retaining obsolete product objects.",
        "Disabled catalog cache flag catalog.cache.enabled, restored replicas, then deployed CATALOG_CACHE_MAX_ENTRIES=5000 and CATALOG_CACHE_TTL_SECONDS=300 with heap alerts. Stable memory returned in 63 minutes.",
        "Raising the pod memory limit from 2GiB to 4GiB only delayed OOMs and caused node pressure. Restarting pods without disabling the unbounded cache repeated the failure.", 63,
    ),
    incident(
        "INC-2025-006", "2025-04-02", "payments-gateway",
        "Payment authorizations began returning 500 immediately after release pgw-2025.04.02.1.",
        """2025-04-02T10:02:31Z payments-gateway: ERROR missing required field settlement_currency
2025-04-02T10:02:32Z POST /authorize -> 500 build=pgw-2025.04.02.1
2025-04-02T10:03:00Z payment_error_rate=18.4%; baseline=0.2%
2025-04-02T10:03:04Z config: PAYMENT_SCHEMA_STRICT_MODE=true""",
        "pgw-2025.04.02.1 enabled PAYMENT_SCHEMA_STRICT_MODE=true before mobile and checkout clients sent settlement_currency.",
        "Rolled back to pgw-2025.04.01.4, set PAYMENT_SCHEMA_STRICT_MODE=false, then deployed the additive schema and updated clients before enabling strict validation. Recovery took 19 minutes.",
        "Restarting the new pods left the incompatible request contract unchanged. Adding a default currency in the database would have misclassified non-USD payments.", 19,
    ),
    incident(
        "INC-2025-007", "2025-04-15", "checkout-edge",
        "External customers could not resolve api.northstar-commerce.com from several public networks.",
        """2025-04-15T08:10:10Z checkout-edge/external-probe: SERVFAIL api.northstar-commerce.com
2025-04-15T08:10:13Z dig @ns1.northstar-commerce.com: REFUSED
2025-04-15T08:10:18Z registrar: NS delegation changed to ns1.legacy-northstar.net at 08:02 UTC
2025-04-15T08:10:22Z route53 hosted zone northstar-commerce-prod: no matching delegation""",
        "The registrar delegation pointed northstar-commerce.com to retired ns1.legacy-northstar.net instead of the Route 53 nameservers for northstar-commerce-prod.",
        "Restored the four Route 53 NS values from zone northstar-commerce-prod, verified the SOA and A records using 1.1.1.1 and 8.8.8.8, and added a registrar-change alert. Resolution took 76 minutes.",
        "Flushing checkout-edge DNS caches could not repair the public authoritative delegation. Editing the retired DNS zone left public resolvers inconsistent.", 76,
    ),
    incident(
        "INC-2025-008", "2025-05-01", "checkout-edge",
        "Portal clients rejected the TLS certificate for api.northstar-commerce.com after midnight renewal failed.",
        """2025-05-01T00:02:01Z checkout-edge/ingress: tls: certificate has expired
2025-05-01T00:02:02Z x509: api.northstar-commerce.com expired at 2025-04-30T23:59:59Z
2025-05-01T00:02:10Z cert-manager: DNS01 challenge timeout for _acme-challenge.api.northstar-commerce.com
2025-05-01T00:02:14Z config: ACME_DNS_CREDENTIAL_REF=secret/acme-prod; secret status=revoked""",
        "The acme-prod DNS credential was revoked, so cert-manager could not complete the DNS01 challenge before the api.northstar-commerce.com certificate expired.",
        "Restored secret/acme-prod with the Route 53 change-record permission, reissued and loaded the certificate, then alerted on ACME_DNS01 failure and CERT_EXPIRY_ALERT_DAYS=21. Recovery took 38 minutes.",
        "Restarting ingress kept serving the expired certificate. Manually copying a certificate without fixing ACME_DNS_CREDENTIAL_REF left the next renewal broken.", 38,
    ),
    incident(
        "INC-2025-009", "2025-05-18", "checkout-edge",
        "Cart sessions were rejected with Redis max-client errors after checkout-edge replica count increased.",
        """2025-05-18T17:44:09Z checkout-edge: redis.exceptions.ConnectionError: max number of clients reached
2025-05-18T17:44:11Z redis: endpoint=payments-session-redis.northstar.internal connected_clients=11997 rejected_connections=126
2025-05-18T17:44:14Z checkout-edge: REDIS_POOL_MAX_SIZE=512; replicas=30; REDIS_MAXCLIENTS=12000
2025-05-18T17:44:18Z checkout-edge: cart session lookup failed; retry budget exhausted""",
        "checkout-edge multiplied REDIS_POOL_MAX_SIZE=512 by 30 replicas, exhausting the 12000-client cap on payments-session-redis; idle connection cleanup was disabled.",
        "Set REDIS_POOL_MAX_SIZE=32 and REDIS_IDLE_TIMEOUT_SECONDS=60, rolled checkout-edge pods gradually, and set REDIS_MAXCLIENTS=12000 only after checking the file-descriptor limit. Cart reads recovered in 31 minutes.",
        "Raising REDIS_MAXCLIENTS to 24000 exceeded the node file-descriptor budget. Restarting Redis before lowering per-replica pools caused a second saturation.", 31,
    ),
    incident(
        "INC-2025-010", "2025-06-04", "orders-api",
        "Order API calls failed with reserved-slot errors and thousands of idle database transactions.",
        """2025-06-04T11:07:41Z orders-api: FATAL: remaining connection slots are reserved
2025-06-04T11:07:46Z pg_stat_activity: database=orders; host=orders-postgres-primary; sessions=596/600
2025-06-04T11:08:02Z orders-api: DB_POOL_MAX_SIZE=80; replicas=8; idle_in_transaction=312
2025-06-04T11:08:05Z query_age=00:19:42; DB_IDLE_TX_TIMEOUT=0""",
        "orders-api release 6.8 stopped closing transactions and DB_IDLE_TX_TIMEOUT=0 disabled cleanup; eight replicas each opened DB_POOL_MAX_SIZE=80 connections.",
        "Rolled back orders-api 6.8, terminated sessions idle for over 5 minutes, set DB_POOL_MAX_SIZE=40 and DB_IDLE_TX_TIMEOUT=60s, then enabled PgBouncer transaction pooling. Recovery took 46 minutes.",
        "Increasing orders-postgres-primary max_connections to 1000 increased memory pressure without releasing leaked transactions. Restarting API pods did not terminate sessions already held by workers.", 46,
    ),
    incident(
        "INC-2025-011", "2025-06-26", "notifications-worker",
        "The shipment-events notification consumer accumulated hours of lag and continuously rebalanced.",
        """2025-06-26T21:15:00Z kafka-lag-exporter: topic=northstar.shipment-events group=notifications-worker lag=302114
2025-06-26T21:15:03Z notifications-worker: KAFKA_MAX_POLL_INTERVAL_MS=300000; PROVIDER_MAX_REQUESTS_PER_SECOND=50
2025-06-26T21:15:08Z webhook-provider: HTTP 429 Retry-After=30s; batch delivery duration=344s
2025-06-26T21:15:12Z group-coordinator: Preparing to rebalance group notifications-worker""",
        "The webhook provider throttled at 5 requests per second, but PROVIDER_MAX_REQUESTS_PER_SECOND=50 caused retries to exceed KAFKA_MAX_POLL_INTERVAL_MS=300000.",
        "Set PROVIDER_MAX_REQUESTS_PER_SECOND=5, PROVIDER_RETRY_BACKOFF_SECONDS=30, and KAFKA_MAX_POLL_INTERVAL_MS=600000; kept consumers within the 32 topic partitions. Lag cleared in 88 minutes.",
        "Adding consumers past the 32 partitions did not increase throughput. Disabling retries dropped customer shipment notifications.", 88,
    ),
    incident(
        "INC-2025-012", "2025-07-09", "checkout-edge",
        "Media and checkout routes returned 502 after the checkout-edge backend pool went empty.",
        """2025-07-09T03:30:33Z checkout-edge/nginx: upstream connect() failed (111: Connection refused)
2025-07-09T03:30:33Z upstream: http://payments-gateway.northstar.svc:8081/health
2025-07-09T03:31:02Z service-discovery: ready_endpoints=0; PAYMENTS_READINESS_PORT=9090
2025-07-09T03:31:06Z payments-gateway: HEALTH_LISTEN_PORT=8081; readiness probe connection refused""",
        "PAYMENTS_READINESS_PORT=9090 did not match payments-gateway HEALTH_LISTEN_PORT=8081, so Kubernetes removed every backend from the checkout-edge upstream pool.",
        "Set PAYMENTS_READINESS_PORT=8081, verified three ready endpoints, and restored checkout-edge traffic only after /health returned 200. 502s cleared in 24 minutes.",
        "Increasing NGINX_PROXY_RETRIES did not help with zero ready endpoints. Restarting checkout-edge before fixing the probe repeatedly loaded an empty upstream pool.", 24,
    ),
    incident(
        "INC-2025-013", "2025-08-11", "search-indexer",
        "Search indexer replicas were OOMKilled as resident memory rose and stayed high after batches completed.",
        """2025-08-11T13:12:00Z search-indexer: rss=3.8GiB memory_limit=4GiB
2025-08-11T13:12:04Z kubelet: search-indexer OOMKilled; restart_count=4
2025-08-11T13:12:09Z config: SEARCH_RESULT_CACHE_MAX_BYTES=0; SEARCH_RESULT_CACHE_TTL_SECONDS=0
2025-08-11T13:12:13Z go_memstats_heap_inuse_bytes=3.6e9; completed_batches=118""",
        "SEARCH_RESULT_CACHE_MAX_BYTES=0 meant unlimited retention and SEARCH_RESULT_CACHE_TTL_SECONDS=0 prevented eviction of large indexed result objects.",
        "Disabled flag search.results_cache.enabled, restored two replicas, then deployed SEARCH_RESULT_CACHE_MAX_BYTES=268435456 and SEARCH_RESULT_CACHE_TTL_SECONDS=120 with heap-profile alarms. Memory stabilized in 71 minutes.",
        "Adding replicas at the same 4GiB memory limit distributed but did not fix the leak. Raising limits to 8GiB pushed nodes into eviction pressure.", 71,
    ),
    incident(
        "INC-2025-014", "2025-09-03", "orders-api",
        "Order status updates returned 500 after release orders-7.18.0 while the migration job was still failing.",
        """2025-09-03T16:05:19Z orders-api: sqlalchemy.exc.ProgrammingError: column settlement_state_v2 does not exist
2025-09-03T16:05:20Z request_id=9be21 build=orders-7.18.0 status=500
2025-09-03T16:06:00Z migration orders_20250903_add_settlement_state: failed (lock timeout)
2025-09-03T16:06:04Z config: ORDER_SETTLEMENT_V2_ENABLED=true""",
        "orders-7.18.0 enabled ORDER_SETTLEMENT_V2_ENABLED=true before migration orders_20250903_add_settlement_state completed on orders-postgres-primary.",
        "Rolled back to orders-7.17.4, set ORDER_SETTLEMENT_V2_ENABLED=false, applied the additive migration in a controlled window, then redeployed after schema verification. Recovery took 29 minutes.",
        "Restarting orders-api pods kept the flag enabled while the column was missing. Re-running the migration during peak writes caused another lock timeout.", 29,
    ),
    incident(
        "INC-2025-015", "2025-10-17", "checkout-edge",
        "api.northstar-commerce.com returned SERVFAIL and some clients also saw TLS handshake failures.",
        """2025-10-17T07:00:00Z checkout-edge/external-probe: SERVFAIL api.northstar-commerce.com via ns2
2025-10-17T07:00:05Z dig +trace: registrar has stale NS value ns2.legacy-northstar.net
2025-10-17T07:00:12Z cert-manager: DNS01 challenge could not resolve _acme-challenge.api.northstar-commerce.com
2025-10-17T07:00:18Z config: TLS_CERT_RENEWAL_WINDOW_HOURS=72; certificate expires in 49h""",
        "A registrar change left ns2.legacy-northstar.net authoritative after the other nameservers moved to Route 53; inconsistent DNS blocked renewal inside TLS_CERT_RENEWAL_WINDOW_HOURS=72.",
        "Removed the stale registrar NS value, verified all four Route 53 nameservers returned the same SOA and TXT records, reissued the certificate, and added probes for each authoritative server. Resolution took 95 minutes.",
        "Renewing TLS before repairing the stale nameserver failed DNS01 validation. Flushing local DNS caches did not change authoritative answers.", 95,
    ),
]


TEST_ALERTS = [
    {
        "label": "payments-gateway Redis pool saturation",
        "alert": "Northstar payments-gateway started rejecting authorizations after a scale-out. payments-session-redis.northstar.internal reports connected_clients=11986 of REDIS_MAXCLIENTS=12000; each of 28 replicas has REDIS_POOL_MAX_SIZE=256.",
    },
    {
        "label": "orders-api database slots exhausted",
        "alert": "Northstar orders-api writes time out with 'remaining connection slots are reserved'. orders-postgres-primary is at 598/600 connections; pg_stat_activity shows 290 idle-in-transaction sessions and DB_IDLE_TX_TIMEOUT=0.",
    },
    {
        "label": "fulfillment Kafka lag and rebalances",
        "alert": "Northstar fulfillment-writers on northstar.order-events has lag=210000 and repeated CommitFailedException. A synchronous database batch takes 330 seconds while KAFKA_MAX_POLL_INTERVAL_MS=300000.",
    },
    {
        "label": "checkout-edge upstream 502",
        "alert": "Northstar checkout-edge returns 502 after payments-gateway release 4.12. Nginx connects to port 8080 but payments-gateway now listens on LISTEN_PORT=8081; errors say connect() failed (111: Connection refused).",
    },
    {
        "label": "Northstar public DNS and certificate failure",
        "alert": "api.northstar-commerce.com is SERVFAIL at public resolvers and cert-manager DNS01 renewal times out. The TLS certificate expires in 36 hours; _acme-challenge TXT lookups fail on ns2.",
    },
    {
        "label": "Schema change consumer error — first occurrence",
        "alert": "First occurrence on catalog-projection: Kafka topic catalog.product.v3 has normal lag, but every record after schema rollout 2026-09-28 fails Avro decoding with UnknownFieldException for field fulfillment_window. Consumer group catalog-search-indexer has restarted 19 times. No prior service incident is known.",
    },
    {
        "label": "Schema change consumer error — second occurrence",
        "alert": "catalog-projection is repeatedly restarting while reading catalog.product.v3 after today's schema update. Lag is still low; Avro deserialization rejects the new fulfillment_window attribute and catalog-search-indexer reports UnknownFieldException on each message.",
    },
]
