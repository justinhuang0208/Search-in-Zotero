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
    where_clause="AND (parentTitle.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR pub.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR date.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR authors.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR attTitle.value LIKE '%${q_escaped}%' ESCAPE '\\'
      OR ia.path LIKE '%${q_escaped}%' ESCAPE '\\')"
  fi

  cat <<SQL
WITH matched_attachments AS (
  SELECT
    ip.itemID AS parent_item_id,
    ip.key AS parent_key,
    COALESCE(parentTitle.value, '(no title)') AS parent_title,
    COALESCE(authors.value, '') AS authors,
    COALESCE(date.value, '') AS date,
    COALESCE(pub.value, '') AS publication,
    ai.itemID AS attachment_item_id,
    ai.key AS attachment_key,
    COALESCE(attTitle.value, '') AS attachment_title,
    ia.path AS raw_path,
    COALESCE(ia.contentType, '') AS content_type,
    COALESCE(ia.storageModTime, strftime('%s', ai.dateAdded)) AS sort_key
  FROM itemAttachments ia
  JOIN items ai ON ai.itemID = ia.itemID
  JOIN items ip ON ip.itemID = ia.parentItemID
  LEFT JOIN itemData idParentTitle ON idParentTitle.itemID = ip.itemID AND idParentTitle.fieldID = 1
  LEFT JOIN itemDataValues parentTitle ON parentTitle.valueID = idParentTitle.valueID
  LEFT JOIN itemData idAttachmentTitle ON idAttachmentTitle.itemID = ai.itemID AND idAttachmentTitle.fieldID = 1
  LEFT JOIN itemDataValues attTitle ON attTitle.valueID = idAttachmentTitle.valueID
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
  WHERE ia.parentItemID IS NOT NULL
    AND ia.path IS NOT NULL
    AND (ia.contentType = 'application/pdf' OR ia.path LIKE '%.pdf')
${where_clause}
),
top_parents AS (
  SELECT parent_item_id, MAX(sort_key) AS parent_sort_key
  FROM matched_attachments
  GROUP BY parent_item_id
  ORDER BY parent_sort_key DESC
  LIMIT ${MAX_RESULTS}
)
SELECT
  m.parent_item_id,
  m.parent_key,
  m.parent_title,
  m.authors,
  m.date,
  m.publication,
  m.attachment_item_id,
  m.attachment_key,
  m.attachment_title,
  m.raw_path,
  m.content_type,
  m.sort_key
FROM matched_attachments m
JOIN top_parents tp ON tp.parent_item_id = m.parent_item_id
ORDER BY tp.parent_sort_key DESC, m.sort_key DESC, m.attachment_item_id DESC;
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
  def resolve_path($raw_path; $attachment_key):
    if ($raw_path | startswith("storage:")) then
      ($base + "/" + $attachment_key + "/" + ($raw_path | sub("^storage:"; "")))
    elif ($raw_path | startswith("file:")) then
      ($raw_path | sub("^file:(//)?"; ""))
    else
      $raw_path
    end;

  def filename:
    (split("/") | last // "");

  def dirname:
    if contains("/") then
      sub("/[^/]+$"; "")
    else
      .
    end;

  [inputs
  | select(length > 0)
  | split("\t") as $c
  | resolve_path($c[9]; $c[7]) as $file_path
  | {
      parentItemID: $c[0],
      parentKey: $c[1],
      parentTitle: $c[2],
      authors: $c[3],
      date: $c[4],
      publication: $c[5],
      attachmentItemID: $c[6],
      attachmentKey: $c[7],
      attachmentTitleRaw: $c[8],
      rawPath: $c[9],
      contentType: $c[10],
      sortKey: ($c[11] | tonumber? // 0),
      filePath: $file_path
    }
  ]
  | sort_by(.parentItemID)
  | group_by(.parentItemID)
  | map(
      sort_by(.sortKey) | reverse as $items
      | $items[0] as $parent
      | {
          parentSortKey: $parent.sortKey,
          title: ($parent.parentTitle // "(no title)"),
          subtitle: ([$parent.authors, $parent.date, $parent.publication] | map(select(length > 0)) | join(" · ")),
          alwaysShowsSubtitle: true,
          badge: ($items | length | tostring),
          children: (
            $items
            | map(
                . as $att
                | ($att.filePath | filename) as $file_name
                | ($att.attachmentTitleRaw | if length > 0 then . else ($file_name | if length > 0 then . else "(untitled attachment)" end) end) as $attachment_title
                | [
                    {
                      title: $attachment_title,
                      subtitle: (["Open original file", $file_name, $att.contentType] | map(select(length > 0)) | join(" · ")),
                      path: $att.filePath,
                      quickLookURL: $att.filePath
                    },
                    {
                      title: ($attachment_title + " · Open in Zotero"),
                      subtitle: "Open this attachment in Zotero",
                      url: ("zotero://open-pdf/library/items/" + $att.attachmentKey)
                    }
                  ]
              )
            | add
          )
        }
    )
  | sort_by(.parentSortKey) | reverse
  | map(del(.parentSortKey))
  '
