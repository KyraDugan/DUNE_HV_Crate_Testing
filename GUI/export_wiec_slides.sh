#!/usr/bin/env bash
# Exports the WIEC slideshow ("wiec_popup_source" in config.json, now WIEC_Component/WIEC_Popups.pdf)
# to <wiec_popup_dir>/<slide>.png (1.png, 2.png, ...) for the WIEC hardware pop-ups
# (WIEC_utils.show_slides). Re-run it after every change to the slideshow, then check that
# "wiec_popup_pages" in config.json still points at the right slides.
#
#     GUI/export_wiec_slides.sh
#
# A PDF is used as it is; any other slideshow (.odp, .pptx) is first turned into a PDF by LibreOffice
# (soffice), which works while the slideshow is open in LibreOffice: the export uses its own
# throwaway LibreOffice profile. Needs poppler-utils (pdftoppm).

set -euo pipefail

repo="$(cd "$(dirname "$0")/.." && pwd)"
config_value() {
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$repo/config.json" "$1"
}
slides="$repo/$(config_value wiec_popup_source)"
out_dir="$repo/$(config_value wiec_popup_dir)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

if [ ! -f "$slides" ]; then
    echo "Slideshow not found: $slides" >&2
    exit 1
fi

if [ "${slides##*.}" = "pdf" ]; then
    pdf="$slides"
else
    soffice -env:UserInstallation="file://$work/profile" --headless \
            --convert-to pdf --outdir "$work" "$slides" > /dev/null 2>&1
    pdf="$work/$(basename "${slides%.*}").pdf"
    if [ ! -f "$pdf" ]; then
        echo "LibreOffice did not export $slides" >&2
        exit 1
    fi
fi

pdftoppm -png -r 150 "$pdf" "$work/slide"

# Start clean, so a slide deleted from the slideshow does not linger as an old PNG
mkdir -p "$out_dir"
rm -f "$out_dir"/*.png
for png in "$work"/slide-*.png; do
    number="${png##*/slide-}"
    number="${number%.png}"
    cp "$png" "$out_dir/$((10#$number)).png"    # slide-07.png -> 7.png
done

echo "Exported $(ls "$out_dir"/*.png | wc -l) slides to $out_dir"
