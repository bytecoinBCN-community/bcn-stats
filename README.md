# bcn-stats

Public Bytecoin (BCN) network figures for https://bytecoin.community/, updated automatically from our own node.

This is an independent community project, not affiliated with the Bytecoin founders or bcndev.

## stats.json

| Field | Meaning | Refreshed |
| --- | --- | --- |
| `updated` | When the figures were collected (UTC) | every 15 minutes |
| `height`, `difficulty` | Top of the chain seen by our node | every 15 minutes |
| `hashrate_khs` | Network hashrate estimated as difficulty / 120 s | every 15 minutes |
| `supply_percent` | Coins generated as a share of 2^64 - 1 atomic units | every 15 minutes |
| `our_share` | Blocks from our X3 among the latest 720 that are in the main chain | hourly |
| `nodes` | Full nodes at the top of the chain, from a P2P handshake scan | every 6 hours |

Nothing is published while our node is behind or has no peers. If `updated` stops moving, the node or its connection is down, so treat the figures as out of date.

`hashrate_khs` is an estimate that lags real hashrate, because difficulty reacts over a 720-block window. Node locations are a manually kept list, and new nodes show as `Unknown`. The nodes listed are not run by us.

## Code

`collect.py` produces this file. The scanner is [bcn-crawl](https://github.com/bytecoinBCN-community/bcn-crawl).

## Licence

LGPLv3.
