"""R4 Performance Optimization Unit Tests.

This package contains unit tests for Round 4 performance optimizations.
Each optimization has a corresponding test file to verify isomorphism
(behavior before == behavior after optimization).

P0 Optimizations (Critical):
- test_dns_async.py: P0-1 Async DNS Resolution
- test_fastcrud_cache.py: P0-2 FastCRUD Flyweight Cache
- test_ip_lru_cache.py: P0-3 IP Address LRU Cache
- test_webhooks_hmac.py: P0-4 Webhooks HMAC (CANCELLED - orjson incompatible)

P1 Optimizations (High Priority):
- test_returning_clause.py: P1-1 RETURNING Clause
- test_cache_warming.py: P1-2 Tenant Cache Warming
- test_worker_config.py: P1-3 Worker max_jobs Config
- test_event_bus_parallel.py: P1-4 Event Bus Parallel Enqueue
- test_ai_streaming_json.py: P1-5 AI Streaming JSON

P2 Optimizations (Medium Priority):
- test_direct_dicts.py: P2-1 Direct Dict Construction
- test_redis_pool.py: P2-2 Redis Pool Optimization
- test_cache_bytes.py: P2-3 Cache Bytes Storage
"""
