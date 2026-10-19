# IBKR Gateway data pacing and limits

> Researched: 2026-09-30. Scope: the TWS socket API through IB Gateway, used by FactorLab's `ib_async` integration. Limits for the separate Client Portal REST Gateway must not be applied to this connection. This note records research; it changes no ingestion behavior.

## General request pacing

The current dedicated documentation defines the per-client-connection limit as **maximum allocated market data lines / 2 requests per second**. Default allocation is 100 lines, giving 50 requests/second; 200 lines gives 100 requests/second. A subscription's initial request counts, while the subsequent incoming data stream does not count as further requests. Database rows and callback counts are therefore not request counts. [IBKR pacing introduction](https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/introduction).

The current changelog dates the higher, allocation-dependent pacing rule to **January 16, 2025**. The August 3, 2026 entry concerns API 10.49 licensing and settlement fields, not the pacing change. Search snippets can conflate adjacent changelog entries. The inspected sources do not specify an exact minimum Gateway build for this pacing change, so the deployed installation's effective allowance must be verified before increasing throughput. [IBKR changelog](https://www.interactivebrokers.com/docs/tws-api/changelog).

There is a documentation inconsistency: the error-100 table still describes 50 messages/second. Prefer the current dedicated pacing rule, but retain a conservative client ceiling until allocation and installed version are known. With the rejection setting enabled, excess requests produce error 100 and three violations terminate the API session. With rejection disabled, Gateway paces excess requests automatically, delaying acknowledgment and potentially increasing latency. [Error codes](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes), [pacing behavior](https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/pacing-behavior).

## Limits by data type

| Data request | Documented limit or behavior | Implication |
|---|---|---|
| Positions, `reqPositions` | Initial complete position list, followed by changes | Subscribe once and use subsequent updates; no separate endpoint-specific request rate is published on its page |
| Account updates, `reqAccountUpdates` | One account subscribed at a time; changed values arrive on position changes or the approximately three-minute account cycle; frequency is fixed | Repeated polling cannot force the underlying account window to update more frequently |
| Account summary, `reqAccountSummary` | Two active subscriptions maximum; initial requested values, then changed values every three minutes | This is a subscription/concurrency cap and refresh cadence, not a rule of two calls per minute |
| Executions, `reqExecutions` | Current day's executions since midnight, filtered by `ExecutionFilter` | No separate execution request rate is published in this method's current page; general pacing still applies |
| Historical bars of 30 seconds or less | Three overlapping pacing checks, detailed below | Use a shared sliding-window limiter and avoid duplicate retries |
| Historical bars of one minute or longer | Older official documentation says the hard small-bar pacing limits were lifted, while soft throttling remains | Do not treat daily bars as an unlimited bulk-data service |
| Real-time bars, `reqRealTimeBars` | Five-second bars only; 60 new queries per 600 seconds; active subscriptions consume Level 1 lines | Keep subscriptions open instead of recreating them for each bar |
| Historical ticks, `reqHistoricalTicks` | At most 1,000 requested ticks; one trading session per request; full-second completion may return additional ticks; history limited to three years | Page by returned data/session; this is a response-size limit, distinct from request rate |
| Live tick-by-tick subscriptions | Approximately 5% of allocated market data lines; one request per instrument per 15 seconds | Default 100 lines allows about five active tick-by-tick streams |
| Level 1 market data | Simultaneous lines shared between TWS watchlists and API clients for the username | Multiple client IDs do not create additional market data line allocation |
| Level 2 market depth | Default 100 lines allows three simultaneous depth subscriptions | Separate cap from Level 1 and tick-by-tick streams |

Sources for the table: [positions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/positions/request-positions), [account updates](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates), [account summary subscription cap](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-summary/requesting-account-summary), [account summary refresh](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-summary/introduction), [execution details](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/execution-details/request-execution-details), [small bars](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/pacing-violations-for-small-bars-30-secs-or-less), [real-time bars](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/5-second-bars/request-real-time-bars), [historical tick request](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-time-sales/requesting-time-and-sales-data), [historical tick availability](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-time-sales/introduction), [live tick-by-tick](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/tick-by-tick-data/introduction), [shared market data lines](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-lines/introduction), [specialized line caps](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-lines/specialized-market-data-lines).

## Historical small-bar pacing

For bars of **30 seconds or less**, a violation occurs when making:

- An identical historical request within 15 seconds of its predecessor.
- Six or more requests for the same contract, exchange and tick type within two seconds. Keep the count below six.
- More than 60 requests within any ten-minute window. `BID_ASK` requests count twice.

These limits overlap with general request pacing; satisfying 50 requests/second does not satisfy historical pacing. The ten-minute cap permits an average of six requests/minute, or roughly one every ten seconds, before adding headroom. For `BID_ASK`, its double weight halves the equivalent rate. These are sliding-window limits, not a fresh allowance at each clock-hour boundary. [Current small-bar rules](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/pacing-violations-for-small-bars-30-secs-or-less).

The older official GitHub documentation states a **50 simultaneous open historical-request cap**, and explicitly says hard pacing limits for bar sizes of one minute and above were lifted, but soft throttling and eventual disconnect remain possible. Requests taking several minutes should be cancelled. That site labels itself deprecated; the current split historical-limitations introduction does not repeat these two statements. Treat them as conservative older official guidance rather than newly verified guarantees, and use a much smaller outstanding-request count. [Deprecated official historical limitations](https://interactivebrokers.github.io/tws-api/historical_limitations.html), [current historical limitations introduction](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-data-limitations/introduction).

The current historical-ticks pages publish the 1,000-tick/session/history limits but do **not** state an endpoint-specific requests-per-second ceiling or explicitly attach the small-bar 60/10-minute rule to `reqHistoricalTicks`. Do not silently invent that equivalence. A conservative local history budget can still be an implementation choice, clearly identified as such. [Historical tick request parameters](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-time-sales/requesting-time-and-sales-data), [historical time and sales overview](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-historical/historical-time-sales/introduction).

## Why an hourly portfolio mirror is comfortably within pacing

An hourly cycle issuing a small number of position/account/order/execution requests is far below the default general ceiling. Hundreds of account-value callbacks returned by one request do not consume hundreds of outbound requests. The historical small-bar limits do not establish an hourly minimum interval for portfolio snapshots. This is an inference from the documented request/subscription model and endpoint scopes, not a measured outbound request trace. [General request model](https://www.interactivebrokers.com/docs/tws-api/doc/pacing-limitations/introduction), [account updates](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates).

A timeout alone does not prove pacing. Capture Gateway API errors and the failed method before diagnosing: error 100 identifies general pacing; 101 identifies exhausted active market data lines; 420 can concern real-time query/pacing issues; 162 is a broader historical-service error and requires inspecting the message text. Entitlement/competing-session errors are separate failure classes. [IBKR error codes](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes).

## Recommended implementation posture

FactorLab inspection on 2026-09-30: `capture.py` reads `portfolio()`,
`accountValues()` and `openTrades()` from the library's local state, while
`capture_executions()` issues a blocking `reqExecutions()` request. `client.py`
sets a 15-second connection/synchronization timeout, but does not configure
`IB.RequestTimeout`. The locked library version is `ib_async` 2.1.0, whose
blocking-request timeout defaults to zero (unbounded), outbound throttle defaults
to 45 requests/second, and startup synchronization errors do not raise by
default. [IB source](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/ib.html),
[client source](https://ib-api-reloaded.github.io/ib_async/_modules/ib_async/client.html).

The 13:42 UTC snapshot remained running after more than 22 minutes; logs later
recorded server-connectivity error 1100. A requested retry required recreating
the snapshot container to clear its leftover temporary lock. The new 14:10:48
UTC run wrote 8 positions and 144 account-state rows, but again logged startup
timeouts and had not completed by 14:12 UTC. This establishes partial writes
and a timeout/availability concern, not a confirmed rate-limit violation.

These are engineering recommendations, not additional IBKR rules:

1. Retain conservative outbound throttling until actual Gateway version and market data allocation are verified; prioritize small portfolio requests over bulk history.
2. Use bounded timeouts on every remote request and an overall snapshot deadline. A connection timeout alone does not bound later execution requests. A stuck serial scheduler can prevent subsequent hourly cycles despite a correctly configured interval.
3. Reuse streaming subscriptions where useful; write hourly database snapshots from current state while checking subscription completion/freshness.
4. Share historical pacing state across related requests and callers. Enforce duplicate, contract/exchange/type burst, weighted ten-minute, and outstanding-request limits when applicable.
5. Distinguish IBKR pacing from local throttles. A locally configured 50 history requests/600 seconds is an intentionally stricter policy, not proof that the same cap is enforced for daily bars or account data.
6. Record request method, elapsed time, end callback, error code/text and disconnect events. Label partial portfolio writes separately from a completed snapshot.

Research verification: current first-party pages and the changelog were opened directly; deprecated guidance is explicitly marked. No configuration, deployment, or ingestion changes were made for this note.
