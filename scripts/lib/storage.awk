# storage.json for the dashboard's Storage tab, from storage-scan.sh's files.
# Run by storage_json() in storage.sh; plain POSIX awk (DSM: no gawk-isms).
#
# Inputs (by file name): subvols.tsv, docker.tsv, items.tsv,
# seasons.tsv, folders.tsv, held.tsv, metadata.tsv, history.tsv. Variables: today, total, used, free, generated, media_share,
# targets ("<today-7> <today-30> <today-90>"), weeks (12 dates, oldest first).
#
# Trend rules:
#   growth   used now minus used on the last day at or before 30 days ago
#            (or the first day of history, if younger); the daily rate is that
#            change divided by the days between.
#   changes  per series, the films together, each shared folder (but the
#            media one, shown by series and film) and Drive's history, over
#            the same period: the 4 largest increases and 2 largest decreases
#            of at least 0.5 GB.

function js(s) {
	gsub(/\\/, "\\\\", s); gsub(/"/, "\\\"", s); gsub(/[\001-\037]/, " ", s)
	return "\"" s "\""
}
function n0(x) { return sprintf("%.0f", x) }
function nn(x) { return (x == "") ? "null" : sprintf("%.0f", x) }
# Days since 1970-01-01 of a YYYY-MM-DD date (civil-from-days, integer math).
function day(s,   y, m, d, era, yoe, doy, doe) {
	y = substr(s, 1, 4) + 0; m = substr(s, 6, 2) + 0; d = substr(s, 9, 2) + 0
	if (m <= 2) y--
	era = int((y >= 0 ? y : y - 399) / 400); yoe = y - era * 400
	doy = int((153 * (m + (m > 2 ? -3 : 9)) + 2) / 5) + d - 1
	doe = yoe * 365 + int(yoe / 4) - int(yoe / 100) + doy
	return era * 146097 + doe - 719468
}
# History value of kind/name on the last recorded day at or before `at`.
function hist(kind, name, at,   i) {
	for (i = ND; i >= 1; i--) if (DT[i] <= at && ((DT[i] FS kind FS name) in H)) return H[DT[i] FS kind FS name]
	return ""
}
function histc(kind, name, at,   i) {
	for (i = ND; i >= 1; i--) if (DT[i] <= at && ((DT[i] FS kind FS name) in C)) return C[DT[i] FS kind FS name]
	return ""
}

FILENAME ~ /subvols\.tsv$/ {
	if ($2 == "share") { share[$1] = $3; nsh++ }
	else if ($2 == "drive") { drive_r = $3; drive_x = $4 }
	else if ($2 == "hbcache") { hb_r = $3; hb_x = $4 }
	else if ($2 == "snapshot") snap_x += $4
	else if ($2 == "system") system_b += ($4 > 0 ? $4 : $3)
	if ($4 > 0 && $2 != "drive") excl_known = 1   # drive_own() measures Drive alone
	next
}
FILENAME ~ /docker\.tsv$/ { docker_total = $1; have_docker = 1; next }
FILENAME ~ /items\.tsv$/ {
	k = $1 FS $2
	ikind[k] = $1; iname[k] = $2; ibytes[k] = $3; ifiles[k] = $4; iep[k] = $5
	isecs[k] = $6
	tot[$1] += $3; cnt[$1]++
	next
}
FILENAME ~ /seasons\.tsv$/ { seasons[$1] = seasons[$1] sprintf("%s[%s, %s, %s]", (seasons[$1] == "" ? "" : ", "), js($2), n0($3), n0($4)); next }
FILENAME ~ /folders\.tsv$/ {
	if ($2 == "total") measured[$1] = $4
	else folders[$1] = folders[$1] sprintf("%s{\"path\": %s, \"bytes\": %s}", (folders[$1] == "" ? "" : ", "), js($3), n0($4))
	next
}
FILENAME ~ /held\.tsv$/ {
	if ($2 == "total") { held[$1] = $4 }
	else heldf[$1] = heldf[$1] sprintf("%s{\"path\": %s, \"bytes\": %s}", (heldf[$1] == "" ? "" : ", "), js($3), n0($4))
	next
}
FILENAME ~ /metadata\.tsv$/ { metadata = $1; next }
FILENAME ~ /history\.tsv$/ {
	if (!($1 in seen)) { seen[$1]; DT[++ND] = $1 }
	H[$1 FS $2 FS $3] = $4; C[$1 FS $2 FS $3] = $5
	if ($2 == "film") filmday[$1] = filmday[$1] " " $3 "\t"
	next
}

END {
	split(targets, T, " "); d7 = T[1]; d30 = T[2]; d90 = T[3]
	nw = split(weeks, W, " ")
	# history.tsv is sorted by date; DT[] is in order.
	base = ""
	for (i = ND; i >= 1; i--) if (DT[i] <= d30) { base = DT[i]; break }
	if (base == "" && ND > 0 && DT[1] < today) base = DT[1]

	printf "{\n  \"generated\": %s,\n  \"today\": %s,\n", generated, js(today)
	printf "  \"history_since\": %s,\n", (ND ? js(DT[1]) : "null")
	printf "  \"volume\": {\"total\": %s, \"used\": %s, \"free\": %s},\n", n0(total), n0(used), n0(free)

	# ---- growth
	if (base != "") {
		g = used - hist("used", "-", base)
		span = day(today) - day(base)
		printf "  \"growth\": {\"since\": %s, \"delta\": %s, \"per_day\": %s},\n", js(base), n0(g), (span > 0 ? n0(g / span) : "null")
	} else printf "  \"growth\": null,\n"

	# ---- where the space goes
	sum = 0; printf "  \"parts\": ["; sep = ""
	for (s in share) { printf "%s\n    {\"name\": %s, \"kind\": \"share\", \"bytes\": %s}", sep, js(s), n0(share[s]); sep = ","; sum += share[s] }
	if (drive_r != "") { v = drive_x > 0 ? drive_x : drive_r; printf "%s\n    {\"name\": \"Drive version history\", \"kind\": \"drive\", \"bytes\": %s}", sep, n0(v); sep = ","; sum += v; drive_v = v }
	if (hb_r != "") { v = hb_x > 0 ? hb_x : hb_r; printf "%s\n    {\"name\": \"Hyper Backup cache\", \"kind\": \"hbcache\", \"bytes\": %s}", sep, n0(v); sep = ","; sum += v }
	if (excl_known && snap_x > 0) { printf "%s\n    {\"name\": \"Snapshots\", \"kind\": \"snapshots\", \"bytes\": %s}", sep, n0(snap_x); sep = ","; sum += snap_x }
	if (have_docker) { printf "%s\n    {\"name\": \"Docker\", \"kind\": \"docker\", \"bytes\": %s}", sep, n0(docker_total); sep = ","; sum += docker_total }
	other = used - sum
	printf "\n  ],\n  \"other\": %s,\n  \"counted_twice\": %s,\n  \"exclusive_known\": %s,\n", \
		n0(other > 0 ? other : 0), n0(other < 0 ? -other : 0), (excl_known ? "true" : "false")

	# ---- used space per day, last 90 days
	printf "  \"daily\": ["; sep = ""
	for (i = 1; i <= ND; i++) if (DT[i] >= d90 && ((DT[i] FS "used" FS "-") in H)) {
		printf "%s[%s, %s]", sep, js(DT[i]), n0(H[DT[i] FS "used" FS "-"]); sep = ", "
	}
	printf "],\n"

	# ---- shared folders
	printf "  \"shares\": ["; sep = ""
	for (s in share) {
		a7 = hist("share", s, d7); a30 = hist("share", s, d30)
		printf "%s\n    {\"name\": %s, \"bytes\": %s, \"d7\": %s, \"d30\": %s, \"weekly\": [", sep, js(s), n0(share[s]),
			(a7 == "" || d7 < DT[1] ? "null" : n0(share[s] - a7)), (a30 == "" || d30 < DT[1] ? "null" : n0(share[s] - a30))
		for (w = 1; w <= nw; w++) printf "%s%s", (w > 1 ? ", " : ""), (w == nw ? n0(share[s]) : nn(hist("share", s, W[w])))
		# Its largest folders, as du measured them: "measured" is du's total
		# for the share, the scale they are drawn to.
		printf "], \"measured\": %s, \"folders\": [%s]}", ((s in measured) ? n0(measured[s]) : "null"), folders[s]; sep = ","
	}
	printf "\n  ],\n"

	# ---- changes since `base`
	nc = 0
	if (base != "") {
		for (s in share) if (s != media_share) { a = hist("share", s, base); if (a != "") { CN[++nc] = s; CK[nc] = "share"; CD[nc] = share[s] - a; CW[nc] = 0 } }
		if (drive_r != "") { a = hist("drive", "-", base); if (a != "") { CN[++nc] = "Drive version history"; CK[nc] = "drive"; CD[nc] = drive_v - a; CW[nc] = 0 } }
		fb = 0; fnew = 0
		for (k in ikind) {
			a = hist(ikind[k], iname[k], base)
			if (ikind[k] == "film") { fb += (a == "" ? 0 : a); if (a == "") fnew++; continue }
			c = histc("series", iname[k], base)
			CN[++nc] = iname[k]; CK[nc] = "series"; CD[nc] = ibytes[k] - (a == "" ? 0 : a)
			CW[nc] = iep[k] - (c == "" ? 0 : c)
		}
		if (cnt["film"] > 0) {
			# Films gone since base still count in the old total.
			n = split(filmday[base], old, "\t")
			for (j = 1; j <= n; j++) { nm = old[j]; sub(/^ /, "", nm); if (nm != "" && !(("film" FS nm) in ikind)) fb += H[base FS "film" FS nm] }
			CN[++nc] = "Films"; CK[nc] = "films"; CD[nc] = tot["film"] - fb; CW[nc] = fnew
		}
	}
	printf "  \"changes\": ["; sep = ""
	for (pass = 1; pass <= 2; pass++) for (r = 1; r <= (pass == 1 ? 4 : 2); r++) {
		best = 0
		for (i = 1; i <= nc; i++) {
			if (i in used_c) continue
			if (pass == 1 && CD[i] >= 5e8 && (best == 0 || CD[i] > CD[best])) best = i
			if (pass == 2 && CD[i] <= -5e8 && (best == 0 || CD[i] < CD[best])) best = i
		}
		if (!best) break
		used_c[best]
		printf "%s\n    {\"name\": %s, \"kind\": %s, \"delta\": %s, \"new\": %s}", sep, js(CN[best]), js(CK[best]), n0(CD[best]), n0(CW[best])
		sep = ","
	}
	printf "\n  ],\n"

	# ---- what "other" (and the snapshots) are made of: btrfs metadata,
	# DSM's own subvolumes, and files deleted from a share that its
	# snapshots still hold (an estimate, from the file sizes)
	printf "  \"other_detail\": {\"metadata\": %s, \"system\": %s, \"snapshot_held\": [", (metadata != "" ? n0(metadata) : "null"), n0(system_b); sep = ""
	for (s in held) { printf "%s\n    {\"share\": %s, \"bytes\": %s, \"folders\": [%s]}", sep, js(s), n0(held[s]), heldf[s]; sep = "," }
	printf "%s]},\n", (sep == "" ? "" : "\n  ")

	# ---- media library: every title, largest first; series with their
	# seasons as [folder, bytes, episodes]
	printf "  \"media\": {\n"
	for (pass = 1; pass <= 2; pass++) {
		kind = (pass == 1 ? "series" : "film")
		printf "    %s: {\"bytes\": %s, \"count\": %s, \"titles\": [", js(kind), n0(tot[kind]), n0(cnt[kind]); sep = ""
		delete taken
		for (r = 1; r <= cnt[kind]; r++) {
			best = ""
			for (k in ikind) if (ikind[k] == kind && !(k in taken) && (best == "" || ibytes[k] > ibytes[best])) best = k
			if (best == "") break
			taken[best]
			printf "%s\n      {\"name\": %s, \"bytes\": %s, \"episodes\": %s, \"gb_per_hour\": %s%s}", \
				sep, js(iname[best]), n0(ibytes[best]), n0(iep[best]), \
				(isecs[best] > 0 ? sprintf("%.2f", ibytes[best] / 1073741824 / (isecs[best] / 3600)) : "null"), \
				(kind == "series" ? ", \"seasons\": [" seasons[iname[best]] "]" : "")
			sep = ","
		}
		printf "\n    ]}%s\n", (pass == 1 ? "," : "")
	}
	printf "  }\n}\n"
}
