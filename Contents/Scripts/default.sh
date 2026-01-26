#!/bin/zsh
set -euo pipefail

export PATH="/opt/homebrew/bin:$PATH"

BASE="/Users/justin/Zotero/storage"
DB_SRC="/Users/justin/Zotero/zotero.sqlite"
MAX_RESULTS=50
query="${*:-}"

if [ ! -f "$DB_SRC" ]; then
  printf '[{"title":"Zotero database not found","subtitle":"%s","badge":"Error"}]\n' "$DB_SRC"
  exit 0
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  printf '[{"title":"sqlite3 not found","subtitle":"Install sqlite3 to query Zotero database","badge":"Error"}]\n'
  exit 0
fi

if ! command -v jq >/dev/null 2>&1; then
  printf '[{"title":"jq not found","subtitle":"Install jq to format results","badge":"Error"}]\n'
  exit 0
fi

tmp_db=""
cleanup() {
  if [ -n "$tmp_db" ] && [ -f "$tmp_db" ]; then
    rm -f "$tmp_db"
  fi
}
trap cleanup EXIT

prepare_db() {
  local tmp
  # macOS mktemp requires the Xs at the end; use -t to avoid collisions.
  tmp="$(mktemp -t zotero.sqlite)"
  if ! sqlite3 "$DB_SRC" ".backup '$tmp'" >/dev/null 2>&1; then
    if ! cp -f "$DB_SRC" "$tmp" >/dev/null 2>&1; then
      printf '[{"title":"Zotero database is locked","subtitle":"Close Zotero or allow db copy","badge":"Error"}]\n'
      exit 0
    fi
  fi
  tmp_db="$tmp"
}

escape_like() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\%/\\%}"
  s="${s//_/\\_}"
  s="${s//\'/\'\'}"
  printf '%s' "$s"
}

build_query() {
  local q="$1"
  local where_clause=""

  if [ -n "$q" ]; then
    local q_escaped
    q_escaped="$(escape_like "$q")"
    where_clause="AND (title.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR pub.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR date.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR authors.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR ia.path LIKE '%${q_escaped}%' ESCAPE '\\')"
  fi

  cat <<SQL
SELECT
  COALESCE(title.value, '(no title)') AS title,
  COALESCE(authors.value, '') AS authors,
  COALESCE(date.value, '') AS date,
  COALESCE(pub.value, '') AS publication,
  ia.path AS raw_path,
  ai.key AS attachment_key,
  COALESCE(ia.storageModTime, strftime('%s', ai.dateAdded)) AS sort_key
FROM itemAttachments ia
JOIN items ai ON ai.itemID = ia.itemID
LEFT JOIN items ip ON ip.itemID = ia.parentItemID
LEFT JOIN itemData idTitle ON idTitle.itemID = ip.itemID AND idTitle.fieldID = 1
LEFT JOIN itemDataValues title ON title.valueID = idTitle.valueID
LEFT JOIN itemData idPub ON idPub.itemID = ip.itemID AND idPub.fieldID = 38
LEFT JOIN itemDataValues pub ON pub.valueID = idPub.valueID
LEFT JOIN itemData idDate ON idDate.itemID = ip.itemID AND idDate.fieldID = 6
LEFT JOIN itemDataValues date ON date.valueID = idDate.valueID
LEFT JOIN (
  SELECT ic.itemID,
         GROUP_CONCAT(
           CASE
             WHEN c.lastName IS NOT NULL AND c.lastName != '' AND c.firstName IS NOT NULL AND c.firstName != '' THEN c.lastName || ' ' || c.firstName
             WHEN c.lastName IS NOT NULL AND c.lastName != '' THEN c.lastName
             ELSE c.firstName
           END,
           ', '
         ) AS value
  FROM itemCreators ic
  JOIN creators c ON c.creatorID = ic.creatorID
  JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID
  WHERE ct.creatorType = 'author'
  GROUP BY ic.itemID
) authors ON authors.itemID = ip.itemID
WHERE ia.path IS NOT NULL
  AND (ia.contentType = 'application/pdf' OR ia.path LIKE '%.pdf')
${where_clause}
ORDER BY sort_key DESC
LIMIT ${MAX_RESULTS};
SQL
}

prepare_db

output="$(
  sqlite3 -separator $'\t' "$tmp_db" "$(build_query "$query")" 2>&1
)"
exit_code=$?
if [ $exit_code -ne 0 ]; then
  printf '[{"title":"Zotero query failed","subtitle":"%s","badge":"Error"}]\n' "$output"
  exit 0
fi
results="$output"

if [ -z "$results" ]; then
  printf '[{"title":"No matches","subtitle":"%s"}]\n' "$query"
  exit 0
fi

printf '%s\n' "$results" | jq -Rn --arg base "$BASE" '
  [inputs
  | split("\t") as $c
  | {
      title: $c[0],
      subtitle: ([$c[1], $c[2], $c[3]] | map(select(length > 0)) | join(" · ")),
      url: ("zotero://open-pdf/library/items/" + $c[5]),
      quickLookURL: (
        if ($c[4] | startswith("storage:")) then
          ($base + "/" + $c[5] + "/" + ($c[4] | sub("^storage:"; "")))
        elif ($c[4] | startswith("file:")) then
          ($c[4] | sub("^file:(//)?"; ""))
        else
          $c[4]
        end
      )
    }
  ]'
