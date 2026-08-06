#!/usr/bin/env bash
# Download arXiv PDFs for 30-paper distribution measurement.
# Rate-limited to 3 seconds between requests per arXiv guidelines.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

count=0
total=$(grep -c '^[0-9]' paper_ids.txt)

while IFS=$' \t' read -r id category title rest; do
    # Skip comments and blank lines
    [[ "$id" =~ ^#.*$ || -z "$id" ]] && continue

    outfile="${id}.pdf"
    if [[ -f "$outfile" ]]; then
        echo "[skip] $outfile already exists ($category)"
        count=$((count + 1))
        continue
    fi

    url="https://arxiv.org/pdf/${id}"
    echo "[${count}/${total}] Downloading $id ($category: $title)..."
    curl -sL -o "$outfile" "$url"

    # Verify it's a PDF
    if head -c 4 "$outfile" | grep -q '%PDF'; then
        echo "  OK: $(du -h "$outfile" | cut -f1)"
    else
        echo "  WARN: $outfile may not be a valid PDF"
    fi

    count=$((count + 1))
    # Rate limit: 3 seconds between requests
    if [[ $count -lt $total ]]; then
        sleep 3
    fi
done < paper_ids.txt

echo ""
echo "Done. $count/$total papers downloaded."
