# Scan post preview payload

Each successful direct scan stores a compact public post snapshot in `lead_radar_scan_requests.result.posts` so the Radar UI can review source quality without making another provider request.

Fields: `id`, `source`, `title`, full `body` (bounded), `published_at`, original `url`, public `author` display data, `images`, public engagement `metrics`, `tags`, final `decision`, and optional `lead_id`.

The snapshot intentionally excludes provider tokens, cookies, private identifiers, and unrelated raw response fields.
