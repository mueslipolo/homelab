//! immich-verify, two commands working on SHA-1, Immich's own checksum:
//!
//! - check: is every photo and video of these folders and archives already in
//!   one or several accounts? Hashes them here, asks Immich which hashes they have
//!   (POST /api/assets/bulk-upload-check), and with --extract copies the
//!   missing ones out for immich-go; --upload also runs immich-go on them and
//!   asks again. Only hashes cross the network, until --upload.
//! - overlap: which photos are in two accounts at once? Lists both accounts'
//!   checksums (POST /api/search/metadata) and compares them. Nothing hashed.
//!
//! README.md has the why.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, IsTerminal, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};

const EXIT_MISSING: i32 = 1;
const EXIT_USAGE: i32 = 2;

// What Immich takes as a photo or a video (its mime-types list, lower case).
// Anything else is listed as "not media": never "present", never "missing".
const MEDIA: &[&str] = &[
    "3fr", "ari", "arw", "avif", "bmp", "cap", "cin", "cr2", "cr3", "crw", "dcr", "dng", "erf",
    "fff", "gif", "heic", "heif", "hif", "iiq", "insp", "jp2", "jpe", "jpeg", "jpg", "jxl", "k25",
    "kdc", "mrw", "nef", "nrw", "orf", "ori", "pef", "png", "psd", "raf", "raw", "rw2", "rwl",
    "sr2", "srf", "srw", "svg", "tif", "tiff", "webp", "x3f", "3gp", "3gpp", "avi", "flv", "insv",
    "m2t", "m2ts", "m4v", "mkv", "mov", "mp4", "mpe", "mpeg", "mpg", "mts", "vob", "webm", "wmv",
];

#[derive(Parser)]
#[command(version, about = "Photos and videos against Immich, by SHA-1 (Immich's own checksum)")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Are these folders and archives in Immich, in one or several accounts? Copies out what is missing
    #[command(after_help = "Each --person reads ~/immich-go/token.<person> (as for \
                  scripts/immich-import.sh), each --key-file a key file; without either, \
                  $IMMICH_API_KEY. A key needs the asset.upload permission, and decides whose \
                  account is checked. With several, a file is present when any of them has it; \
                  report.tsv's last column says who, and --extract copies only what none has \
                  (to upload to the first one).\n\n\
                  --upload runs immich-go on what it copied out, into the first account, \
                  then asks Immich again: report.tsv shows what arrived. immich-go is \
                  $IMMICH_GO, else on the PATH, else ~/immich-go/immich-go. The keys reach it \
                  in a config file only you can read, removed after. An admin's key (any \
                  --person who is one) pauses Immich's jobs during the upload.\n\n\
                  Exit codes: 0 everything is in Immich, 1 something is missing, trashed or \
                  unreadable (see report.tsv), 2 usage or a fatal error.\n\n\
                  Example:\n  immich-verify check --server https://immich.home.example.com \
                  --person alice --person bob --extract ~/Takeout/takeout-*.zip ~/Pictures/Phone\n  \
                  immich-verify check --server https://immich.home.example.com \
                  --person alice --person bob --upload ~/Takeout -- --include-unmatched")]
    Check(Args),
    /// Which photos are in two accounts at once (the same file uploaded by both)?
    #[command(after_help = "Each account is a login (key in ~/immich-go/token.<login>) or the \
                  path of a key file. The keys need the asset.read and user.read permissions. \
                  A partner's photos shown through Partner Sharing are not counted: only what \
                  each account owns.\n\n\
                  Example:\n  immich-verify overlap --server https://immich.home.example.com \
                  alice bob")]
    Overlap(OverlapArgs),
}

#[derive(clap::Args)]
struct OverlapArgs {
    /// The two accounts: logins or key files
    #[arg(num_args = 2, required = true)]
    accounts: Vec<String>,

    /// Immich's address [env: IMMICH_SERVER]
    #[arg(long)]
    server: Option<String>,

    /// Where overlap.tsv goes [default: ./immich-overlap-<date>-<time>]
    #[arg(long)]
    out: Option<PathBuf>,
}

#[derive(clap::Args)]
struct Args {
    /// Folders, .zip, .tar, .tar.gz or .tgz files
    #[arg(required = true)]
    sources: Vec<PathBuf>,

    /// Immich's address [env: IMMICH_SERVER]
    #[arg(long)]
    server: Option<String>,

    /// Whose account: reads the key from ~/immich-go/token.<PERSON>; repeat for several
    #[arg(long)]
    person: Vec<String>,

    /// File holding an API key; repeat for several
    #[arg(long)]
    key_file: Vec<PathBuf>,

    /// Where report.tsv (and missing/) go [default: ./immich-verify-<date>-<time>]
    #[arg(long)]
    out: Option<PathBuf>,

    /// Copy the missing files to <out>/missing/, with the .json next to them
    #[arg(long)]
    extract: bool,

    /// Upload the missing files to the first account with immich-go (copies them out as --extract)
    #[arg(long, conflicts_with = "offline")]
    upload: bool,

    /// With --upload: immich-go only says what it would send
    #[arg(long, requires = "upload")]
    dry_run: bool,

    /// Hash and report, but skip Immich (every hash is "unchecked")
    #[arg(long)]
    offline: bool,

    /// Hashes in one request to Immich
    #[arg(long, default_value_t = 1000)]
    batch: usize,

    /// immich-go's own options for --upload, after -- (e.g. -- --include-unmatched)
    #[arg(last = true)]
    immich_go: Vec<String>,
}

#[derive(Clone, Copy, PartialEq)]
enum SourceKind {
    Folder,
    Zip,
    Tar { gzip: bool },
}

struct Source {
    path: PathBuf,
    // Unique short name: the folder in missing/ and the report's source column.
    name: String,
    kind: SourceKind,
    // Size and mtime of the archive itself, part of its entries' cache keys.
    stamp: String,
}

#[derive(Clone, Copy, PartialEq)]
enum Kind {
    Media,
    // Google Takeout's per-photo metadata and album files: extracted next to
    // the missing photos so immich-go keeps their dates and albums.
    Json,
    Other,
}

#[derive(Clone, PartialEq)]
enum Status {
    Present,
    Missing,
    Trashed,
    NotMedia,
    Ignored,
    Empty,
    Unchecked,
    Error(String),
}

impl Status {
    fn label(&self) -> &str {
        match self {
            Status::Present => "present",
            Status::Missing => "missing",
            Status::Trashed => "trashed",
            Status::NotMedia => "not-media",
            Status::Ignored => "ignored",
            Status::Empty => "empty",
            Status::Unchecked => "unchecked",
            Status::Error(_) => "error",
        }
    }
}

struct Item {
    src: usize,
    // Path inside the source, '/'-separated.
    path: String,
    size: u64,
    kind: Kind,
    // Folder: the file. Zip: the entry's index. Tar: unused (read in order).
    file: Option<PathBuf>,
    zip_index: usize,
    mtime: Option<SystemTime>,
    key: String,
    sha1: Option<String>,
    // The hash came from the cache, not from reading the file.
    cached: bool,
    // The accounts that have it: "alice,bob", "bob (trash)".
    holders: String,
    status: Status,
}

fn main() {
    let result = match Cli::parse().command {
        Command::Check(args) => run(args),
        Command::Overlap(args) => overlap(args),
    };
    match result {
        Ok(code) => std::process::exit(code),
        Err(e) => {
            eprintln!("immich-verify: {e:#}");
            std::process::exit(EXIT_USAGE);
        }
    }
}

fn run(args: Args) -> Result<i32> {
    let sources = open_sources(&args.sources)?;
    // Settled before hours of hashing, not after.
    let accounts = if args.offline { Vec::new() } else { accounts(&args)? };
    let immich_go = if args.upload { Some(find_immich_go()?) } else { None };
    let out = args.out.clone().unwrap_or_else(|| PathBuf::from(format!("immich-verify-{}", now_stamp())));
    if out.join("report.tsv").exists() {
        bail!("{} already holds a report.tsv: pick another --out", out.display());
    }
    // Here, not after the hashing: a read-only current folder must fail now.
    fs::create_dir_all(&out).with_context(|| format!("creating {}", out.display()))?;

    // Listing a big folder over SMB takes minutes: counted as it goes. A tar
    // is listed while it is hashed.
    let mut items = Vec::new();
    for (i, s) in sources.iter().enumerate() {
        if matches!(s.kind, SourceKind::Tar { .. }) {
            continue;
        }
        let found = Arc::new(AtomicU64::new(0));
        let (f, name) = (found.clone(), s.name.clone());
        let progress = Progress::start(move |_| format!("listing {name}: {} files", num(f.load(Ordering::Relaxed))));
        let listed = list(i, s, &found);
        progress.stop();
        let listed = listed?;
        let media: Vec<&Item> = listed.iter().filter(|it| it.status == Status::Unchecked).collect();
        eprintln!("{}: {} files, {} photos and videos ({})", s.name, num(listed.len() as u64), num(media.len() as u64),
            size(media.iter().map(|it| it.size).sum()));
        items.extend(listed);
    }

    let started = std::time::Instant::now();
    let mut cache = Cache::open()?;
    let read = hash_all(&sources, &mut items, &mut cache)?;
    let hash_time = started.elapsed();
    cache.save()?;

    let asked = std::time::Instant::now();
    let (hashes, requests) = if accounts.is_empty() {
        items.iter_mut().filter(|it| it.sha1.is_some()).for_each(|it| it.status = Status::Unchecked);
        (0, 0)
    } else {
        check(&accounts, &mut items, args.batch)?
    };
    let ask_time = asked.elapsed();

    write_report(&out.join("report.tsv"), &sources, &items)?;
    summary(&sources, &items);

    let missing = items.iter().any(|it| it.status == Status::Missing);
    let (mut copied, mut uploaded) = (None, None);
    if (args.extract || args.upload) && missing {
        let dir = out.join("missing");
        copied = Some(extract(&sources, &items, &dir)?);
        match &immich_go {
            Some(bin) => {
                uploaded = upload(bin, &sources, &mut items, &dir, &out, &args, &accounts)?;
                write_report(&out.join("report.tsv"), &sources, &items)?;
            }
            None => upload_hint(&sources, &items, &dir, &args, &accounts[0]),
        }
    }

    // The numbers of the run, after the verdict.
    let media: Vec<&Item> = items.iter().filter(|it| it.kind == Kind::Media && it.status != Status::Ignored).collect();
    let distinct = media.iter().filter_map(|it| it.sha1.as_deref()).collect::<HashSet<_>>().len();
    let hashed: Vec<&&Item> = media.iter().filter(|it| it.sha1.is_some() && !it.cached).collect();
    let from_cache = media.iter().filter(|it| it.cached).count();
    eprintln!();
    eprintln!("photos and videos  {} ({}), {} distinct{}", num(media.len() as u64), size(media.iter().map(|it| it.size).sum()), num(distinct as u64),
        if media.len() > distinct { format!(": {} are copies of another one", num((media.len() - distinct) as u64)) } else { String::new() });
    let cache_note = if from_cache > 0 { format!("; {} from the cache", num(from_cache as u64)) } else { String::new() };
    if hashed.is_empty() {
        eprintln!("read               nothing new{cache_note}");
    } else {
        eprintln!("read               {} files, {} in {} ({}/s){cache_note}", num(hashed.len() as u64), size(read), secs(hash_time),
            size((read as f64 / hash_time.as_secs_f64().max(0.001)) as u64));
    }
    if requests > 0 {
        let per = if accounts.len() > 1 { format!(" for each of {} accounts", accounts.len()) } else { String::new() };
        eprintln!("asked Immich       {} hashes{per} in {} request{}, {}", num(hashes as u64), num(requests as u64), if requests == 1 { "" } else { "s" }, secs(ask_time));
    }
    if accounts.len() > 1 {
        // Who has what, by file: each combination of accounts that occurs.
        let mut by: Vec<(String, u64)> = Vec::new();
        for it in media.iter().filter(|it| matches!(it.status, Status::Present | Status::Trashed | Status::Missing)) {
            let who = if it.status == Status::Missing { "none".to_string() } else { it.holders.clone() };
            match by.iter_mut().find(|(w, _)| *w == who) {
                Some((_, n)) => *n += 1,
                None => by.push((who, 1)),
            }
        }
        by.sort_by(|a, b| b.1.cmp(&a.1));
        let line: Vec<String> = by.iter().map(|(w, n)| format!("{} {}", if w == "none" { "in none".to_string() } else { w.replace(',', " and ") }, num(*n))).collect();
        eprintln!("by account         {}", line.join(", "));
    }
    if let Some((n, bytes, json, linked)) = copied {
        let how = match linked {
            0 => String::new(),
            l if l == n + json => ": hard links, no space used".to_string(),
            l => format!(", {} of them hard links", num(l)),
        };
        eprintln!("copied out         {} photos and videos ({}) and {} .json{how}", num(n), size(bytes), num(json));
    }
    match uploaded {
        Some((arrived, still)) => eprintln!("uploaded           {} now in {}'s account{}", num(arrived), accounts[0].label,
            if still > 0 { format!(", {} still missing (immich-go's log in {})", num(still), out.display()) } else { String::new() }),
        None if args.upload && args.dry_run && missing => eprintln!("uploaded           nothing: a dry run (immich-go's counts above)"),
        None => {}
    }
    eprintln!("took               {}", secs(started.elapsed()));
    eprintln!("report: {}", out.join("report.tsv").display());

    // "unchecked" only comes from --offline: not a failure.
    let bad = items.iter().any(|it| matches!(it.status, Status::Missing | Status::Trashed | Status::Error(_)));
    Ok(if bad { EXIT_MISSING } else { 0 })
}

// ------------------------------------------------------------------ sources

fn open_sources(paths: &[PathBuf]) -> Result<Vec<Source>> {
    let mut sources: Vec<Source> = Vec::new();
    for p in paths {
        let meta = fs::metadata(p).with_context(|| format!("{}", p.display()))?;
        let lower = p.to_string_lossy().to_lowercase();
        let kind = if meta.is_dir() {
            SourceKind::Folder
        } else if lower.ends_with(".zip") {
            SourceKind::Zip
        } else if lower.ends_with(".tar") {
            SourceKind::Tar { gzip: false }
        } else if lower.ends_with(".tar.gz") || lower.ends_with(".tgz") {
            SourceKind::Tar { gzip: true }
        } else {
            bail!("{}: not a folder, .zip, .tar, .tar.gz or .tgz", p.display());
        };
        let path = fs::canonicalize(p)?;
        let base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| "root".into());
        // Two sources with the same name (two Photos folders) get -2, -3...
        let mut name = base.clone();
        let mut n = 1;
        while sources.iter().any(|s| s.name == name) {
            n += 1;
            name = format!("{base}-{n}");
        }
        let stamp = format!("{}:{}", meta.len(), mtime_ns(&meta));
        sources.push(Source { path, name, kind, stamp });
    }
    Ok(sources)
}

fn mtime_ns(meta: &fs::Metadata) -> u128 {
    meta.modified().ok().and_then(|t| t.duration_since(UNIX_EPOCH).ok()).map(|d| d.as_nanos()).unwrap_or(0)
}

// Files nobody wants in Immich: DSM's and the OSes' own, AppleDouble halves.
fn ignored(path: &str) -> bool {
    let name = path.rsplit('/').next().unwrap_or(path);
    path.split('/').any(|c| matches!(c, "@eaDir" | "#recycle" | "#snapshot" | "__MACOSX" | ".Trash-1000"))
        || matches!(name, ".DS_Store" | "Thumbs.db" | "desktop.ini" | ".picasa.ini")
        || name.starts_with("._")
}

fn kind_of(path: &str) -> Kind {
    let ext = path.rsplit_once('.').map(|(_, e)| e.to_lowercase()).unwrap_or_default();
    if MEDIA.contains(&ext.as_str()) {
        Kind::Media
    } else if ext == "json" {
        Kind::Json
    } else {
        Kind::Other
    }
}

fn item(src: usize, path: String, size: u64, key: String) -> Item {
    let kind = kind_of(&path);
    let status = if ignored(&path) {
        Status::Ignored
    } else if kind != Kind::Media {
        Status::NotMedia
    } else if size == 0 {
        Status::Empty
    } else {
        Status::Unchecked
    };
    Item { src, path, size, kind, file: None, zip_index: 0, mtime: None, key, sha1: None, cached: false, holders: String::new(), status }
}

fn list(src: usize, s: &Source, found: &AtomicU64) -> Result<Vec<Item>> {
    let mut items = Vec::new();
    match s.kind {
        SourceKind::Folder => {
            for entry in walkdir::WalkDir::new(&s.path).follow_links(false) {
                let entry = entry?;
                if !entry.file_type().is_file() {
                    continue;
                }
                let meta = entry.metadata()?;
                let rel = entry.path().strip_prefix(&s.path)?.to_string_lossy().replace('\\', "/");
                let key = format!("f|{}|{}|{}", entry.path().display(), meta.len(), mtime_ns(&meta));
                let mut it = item(src, rel, meta.len(), key);
                it.file = Some(entry.path().to_path_buf());
                it.mtime = meta.modified().ok();
                items.push(it);
                found.fetch_add(1, Ordering::Relaxed);
            }
        }
        SourceKind::Zip => {
            let mut zip = zip::ZipArchive::new(File::open(&s.path)?)
                .with_context(|| format!("{}: not a readable zip", s.path.display()))?;
            for i in 0..zip.len() {
                let e = zip.by_index_raw(i)?;
                if e.is_dir() {
                    continue;
                }
                let name = e.name().with_context(|| format!("{}: entry {i}", s.path.display()))?.into_owned();
                let key = format!("z|{}|{}|{}|{}|{}", s.path.display(), s.stamp, name, e.size(), e.crc32());
                let mut it = item(src, name, e.size(), key);
                it.zip_index = i;
                items.push(it);
                found.fetch_add(1, Ordering::Relaxed);
            }
        }
        // A tar is read once, in order: listed and hashed in the same pass.
        SourceKind::Tar { .. } => {}
    }
    Ok(items)
}

// ------------------------------------------------------------------ hashing

fn sha1_of(mut r: impl Read, done: &AtomicU64) -> io::Result<String> {
    let mut h = Sha1::new();
    let mut buf = vec![0u8; 1 << 20];
    loop {
        let n = r.read(&mut buf)?;
        if n == 0 {
            break;
        }
        h.update(&buf[..n]);
        done.fetch_add(n as u64, Ordering::Relaxed);
    }
    Ok(hex::encode(h.finalize()))
}

// Returns the bytes read.
fn hash_all(sources: &[Source], items: &mut Vec<Item>, cache: &mut Cache) -> Result<u64> {
    for it in items.iter_mut() {
        if it.status == Status::Unchecked {
            it.sha1 = cache.get(&it.key);
            it.cached = it.sha1.is_some();
        }
    }
    let to_read: Vec<&Item> = items.iter().filter(|it| it.status == Status::Unchecked && it.sha1.is_none()).collect();
    let (todo_files, todo) = (to_read.len() as u64, to_read.iter().map(|it| it.size).sum::<u64>());
    let cached = items.iter().filter(|it| it.cached).count() as u64;
    // A tar's entries are only known as it is read: no total to show.
    let tar = sources.iter().any(|s| matches!(s.kind, SourceKind::Tar { .. }));
    if tar {
        eprintln!("hashing: {} hashes from the cache, the rest as the archives are read", num(cached));
    } else if todo_files == 0 {
        eprintln!("hashing: nothing to read, every hash is in the cache");
    } else {
        eprintln!("hashing: {} files to read ({}), {} hashes from the cache", num(todo_files), size(todo), num(cached));
    }
    let done = Arc::new(AtomicU64::new(0));
    let files = Arc::new(AtomicU64::new(0));
    let (d, f) = (done.clone(), files.clone());
    let progress = Progress::start(move |t| {
        let (d, f) = (d.load(Ordering::Relaxed), f.load(Ordering::Relaxed));
        if tar {
            format!("hashing {} files, {}{}", num(f), size(d), rate_eta(d, None, t))
        } else {
            format!("hashing {} / {} files, {} / {}{}", num(f), num(todo_files), size(d), size(todo), rate_eta(d, Some(todo), t))
        }
    });

    for (src, s) in sources.iter().enumerate() {
        match s.kind {
            SourceKind::Folder => {
                items.par_iter_mut().filter(|it| it.src == src && it.status == Status::Unchecked && it.sha1.is_none()).for_each(|it| {
                    let path = it.file.as_ref().expect("folder items have a file");
                    match File::open(path).and_then(|f| sha1_of(f, &done)) {
                        Ok(h) => it.sha1 = Some(h),
                        Err(e) => it.status = Status::Error(e.to_string()),
                    }
                    files.fetch_add(1, Ordering::Relaxed);
                });
            }
            // One reader per thread, each with its own file handle: entries
            // are inflated side by side. A few threads only: on a disk that
            // seeks, more would be slower, not faster.
            SourceKind::Zip => {
                items.par_iter_mut().filter(|it| it.src == src && it.status == Status::Unchecked && it.sha1.is_none()).for_each_init(
                    || File::open(&s.path).map_err(|e| e.to_string()).and_then(|f| zip::ZipArchive::new(f).map_err(|e| e.to_string())),
                    |zip, it| {
                        let result = match zip {
                            Ok(zip) => zip.by_index(it.zip_index).map_err(|e| e.to_string()).and_then(|e| sha1_of(e, &done).map_err(|e| e.to_string())),
                            Err(e) => Err(e.clone()),
                        };
                        match result {
                            Ok(h) => it.sha1 = Some(h),
                            Err(e) => it.status = Status::Error(e),
                        }
                        files.fetch_add(1, Ordering::Relaxed);
                    },
                );
            }
            SourceKind::Tar { gzip } => items.extend(hash_tar(src, s, gzip, cache, &done, &files)?),
        }
    }
    progress.stop();
    for it in items.iter() {
        if let (Some(h), false) = (&it.sha1, matches!(it.status, Status::Error(_))) {
            cache.put(&it.key, h);
        }
    }
    Ok(done.load(Ordering::Relaxed))
}

fn tar_reader(s: &Source, gzip: bool) -> Result<tar::Archive<Box<dyn Read>>> {
    let f = BufReader::with_capacity(1 << 20, File::open(&s.path)?);
    let r: Box<dyn Read> = if gzip { Box::new(flate2::read::MultiGzDecoder::new(f)) } else { Box::new(f) };
    Ok(tar::Archive::new(r))
}

fn hash_tar(src: usize, s: &Source, gzip: bool, cache: &Cache, done: &AtomicU64, files: &AtomicU64) -> Result<Vec<Item>> {
    let mut items = Vec::new();
    let mut tar = tar_reader(s, gzip)?;
    for entry in tar.entries().with_context(|| format!("{}: not a readable tar", s.path.display()))? {
        let entry = entry?;
        if !entry.header().entry_type().is_file() {
            continue;
        }
        let path = entry.path()?.to_string_lossy().into_owned();
        let size = entry.size();
        let mtime = entry.header().mtime().ok();
        let key = format!("t|{}|{}|{}|{}|{}", s.path.display(), s.stamp, path, size, mtime.unwrap_or(0));
        let mut it = item(src, path, size, key);
        it.mtime = mtime.map(|t| UNIX_EPOCH + Duration::from_secs(t));
        if it.status == Status::Unchecked {
            it.sha1 = cache.get(&it.key);
            it.cached = it.sha1.is_some();
            if it.sha1.is_none() {
                match sha1_of(entry, done) {
                    Ok(h) => it.sha1 = Some(h),
                    Err(e) => it.status = Status::Error(e.to_string()),
                }
                files.fetch_add(1, Ordering::Relaxed);
            }
        }
        items.push(it);
    }
    Ok(items)
}

// Hashes already computed, by (path, size, mtime) for files and (archive,
// size, mtime, entry) for archives: a second run only hashes what is new.
// One line per hash: SHA-1 of the key, then the file's SHA-1.
struct Cache {
    path: PathBuf,
    known: HashMap<String, String>,
    added: Vec<(String, String)>,
}

impl Cache {
    fn open() -> Result<Cache> {
        let dir = std::env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".cache")))
            .unwrap_or_else(|| PathBuf::from("."))
            .join("immich-verify");
        let path = dir.join("hashes.txt");
        let mut known = HashMap::new();
        if let Ok(f) = File::open(&path) {
            for line in BufReader::new(f).lines() {
                if let Some((k, v)) = line?.split_once(' ') {
                    known.insert(k.to_string(), v.to_string());
                }
            }
        }
        Ok(Cache { path, known, added: Vec::new() })
    }

    fn id(key: &str) -> String {
        hex::encode(Sha1::digest(key.as_bytes()))
    }

    fn get(&self, key: &str) -> Option<String> {
        self.known.get(&Self::id(key)).cloned()
    }

    fn put(&mut self, key: &str, sha1: &str) {
        let id = Self::id(key);
        if !self.known.contains_key(&id) {
            self.known.insert(id.clone(), sha1.to_string());
            self.added.push((id, sha1.to_string()));
        }
    }

    fn save(&self) -> Result<()> {
        if self.added.is_empty() {
            return Ok(());
        }
        fs::create_dir_all(self.path.parent().expect("cache has a folder"))?;
        let mut f = fs::OpenOptions::new().create(true).append(true).open(&self.path)?;
        for (k, v) in &self.added {
            writeln!(f, "{k} {v}")?;
        }
        Ok(())
    }
}

// The status line of a long step, from `render` (given the time since the
// step began): redrawn every second on a terminal, and in a pipe or a log a
// full line every 30 s, so a run of hours is never silent.
struct Progress {
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Progress {
    fn start(render: impl Fn(Duration) -> String + Send + 'static) -> Progress {
        let stop = Arc::new(AtomicBool::new(false));
        let tty = io::stderr().is_terminal();
        let thread = {
            let stop = stop.clone();
            std::thread::spawn(move || {
                let started = Instant::now();
                let mut logged = Instant::now();
                'run: loop {
                    // In small steps: a quick step ends without waiting.
                    for _ in 0..10 {
                        if stop.load(Ordering::Relaxed) {
                            break 'run;
                        }
                        std::thread::sleep(Duration::from_millis(100));
                    }
                    if tty {
                        eprint!("\r{}\x1b[K", render(started.elapsed()));
                    } else if logged.elapsed() >= Duration::from_secs(30) {
                        eprintln!("{}", render(started.elapsed()));
                        logged = Instant::now();
                    }
                }
                if tty {
                    eprint!("\r\x1b[K");
                }
            })
        };
        Progress { stop, thread: Some(thread) }
    }

    fn stop(self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread {
            let _ = t.join();
        }
    }
}

// ", 45.0 MB/s, 18 min left" once there is enough to tell; no "left"
// without a total.
fn rate_eta(done: u64, todo: Option<u64>, t: Duration) -> String {
    let t = t.as_secs_f64();
    if t < 3.0 || done == 0 {
        return String::new();
    }
    let rate = done as f64 / t;
    let mut s = format!(", {}/s", size(rate as u64));
    if let Some(todo) = todo {
        let left = todo.saturating_sub(done) as f64 / rate;
        s += &format!(", {} left", eta(Duration::from_secs_f64(left)));
    }
    s
}

// "18 min", "2 h 5 min": no seconds, the rate moves too much for them.
fn eta(d: Duration) -> String {
    if d.as_secs() < 60 {
        return "under a minute".into();
    }
    let m = d.as_secs().div_ceil(60);
    if m < 60 { format!("{m} min") } else { format!("{} h {} min", m / 60, m % 60) }
}

// ------------------------------------------------------------------- immich

struct Immich {
    // https://immich.<domain>, without /api.
    base: String,
    key: String,
}

#[derive(Serialize)]
struct CheckItem<'a> {
    id: String,
    checksum: &'a str,
}

#[derive(Serialize)]
struct CheckRequest<'a> {
    assets: Vec<CheckItem<'a>>,
}

#[derive(Deserialize)]
struct CheckResponse {
    results: Vec<CheckResult>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct CheckResult {
    id: String,
    action: String,
    reason: Option<String>,
    is_trashed: Option<bool>,
}

fn server(arg: &Option<String>) -> Result<String> {
    let server = arg
        .clone()
        .or_else(|| std::env::var("IMMICH_SERVER").ok())
        .context("no server: --server https://immich.<domain> (or IMMICH_SERVER)")?;
    Ok(server.trim_end_matches('/').trim_end_matches("/api").to_string())
}

// ~/immich-go/token.<login>, the file scripts/immich-import.sh reads too.
fn person_key(login: &str) -> Result<String> {
    let home = std::env::var_os("HOME").context("no HOME for ~/immich-go")?;
    read_key(&PathBuf::from(home).join("immich-go").join(format!("token.{login}")))
}

fn api_error(url: &str, e: ureq::Error, permission: &str) -> anyhow::Error {
    match e {
        ureq::Error::StatusCode(401) | ureq::Error::StatusCode(403) => {
            anyhow::anyhow!("Immich refused the API key at {url} ({e}): it needs the {permission} permission")
        }
        e => anyhow::anyhow!("asking {url}: {e}"),
    }
}

// One account to check: its name in the report, the key file to give
// immich-go, and its connection.
struct Who {
    label: String,
    key_file: String,
    immich: Immich,
}

fn accounts(args: &Args) -> Result<Vec<Who>> {
    let base = server(&args.server)?;
    let mut who = Vec::new();
    for p in &args.person {
        who.push(Who { label: p.clone(), key_file: format!("~/immich-go/token.{p}"), immich: Immich::new(base.clone(), person_key(p)?)? });
    }
    for f in &args.key_file {
        let label = f.file_name().map(|n| n.to_string_lossy().trim_start_matches("token.").to_string()).unwrap_or_else(|| f.display().to_string());
        who.push(Who { label, key_file: f.display().to_string(), immich: Immich::new(base.clone(), read_key(f)?)? });
    }
    if who.is_empty() {
        match std::env::var("IMMICH_API_KEY") {
            Ok(k) => who.push(Who { label: "key".into(), key_file: "<key file>".into(), immich: Immich::new(base, k.trim().to_string())? }),
            Err(_) => bail!("no API key: --person <login> (~/immich-go/token.<login>), --key-file, or IMMICH_API_KEY"),
        }
    }
    let mut labels: Vec<&str> = who.iter().map(|w| w.label.as_str()).collect();
    labels.sort();
    labels.dedup();
    if labels.len() < who.len() {
        bail!("the same account twice: {}", who.iter().map(|w| w.label.as_str()).collect::<Vec<_>>().join(", "));
    }
    Ok(who)
}

// Each distinct hash once per account. A file is present when any account
// has it outside the trash, trashed when only a trash has it, missing when
// none has it. Returns how many hashes were asked, in how many requests.
fn check(accounts: &[Who], items: &mut [Item], batch: usize) -> Result<(usize, usize)> {
    let mut hashes: Vec<String> = items.iter().filter(|it| it.status == Status::Unchecked).filter_map(|it| it.sha1.clone()).collect();
    hashes.sort();
    hashes.dedup();
    let requests = (accounts.len() * hashes.len().div_ceil(batch.max(1))) as u64;
    if requests == 0 {
        return Ok((0, 0));
    }
    eprintln!("asking Immich about {} hashes, in {}'s account{}", num(hashes.len() as u64),
        accounts.iter().map(|w| w.label.as_str()).collect::<Vec<_>>().join(" and "), if accounts.len() == 1 { "" } else { "s" });
    let asked = Arc::new(AtomicU64::new(0));
    let a = asked.clone();
    let progress = Progress::start(move |_| format!("asking Immich: {} / {} requests", num(a.load(Ordering::Relaxed)), num(requests)));
    let answers: Result<Vec<HashMap<String, Status>>> = accounts.iter().map(|w| w.immich.lookup(&hashes, batch, &asked)).collect();
    progress.stop();
    let answers = answers?;
    for it in items.iter_mut().filter(|it| it.status == Status::Unchecked) {
        let Some(h) = &it.sha1 else { continue };
        let (mut present, mut trashed, mut error) = (Vec::new(), Vec::new(), None);
        for (w, answer) in accounts.iter().zip(&answers) {
            match answer.get(h) {
                Some(Status::Present) => present.push(w.label.clone()),
                Some(Status::Trashed) => trashed.push(format!("{} (trash)", w.label)),
                Some(Status::Missing) => {}
                Some(Status::Error(e)) => error = Some(format!("{}: {e}", w.label)),
                _ => error = Some(format!("{}: no answer from Immich", w.label)),
            }
        }
        it.status = if !present.is_empty() {
            Status::Present
        } else if let Some(e) = error {
            Status::Error(e)
        } else if !trashed.is_empty() {
            Status::Trashed
        } else {
            Status::Missing
        };
        present.extend(trashed);
        it.holders = present.join(",");
    }
    Ok((hashes.len(), accounts.len() * hashes.len().div_ceil(batch.max(1))))
}

impl Immich {
    fn new(base: String, key: String) -> Result<Immich> {
        if key.is_empty() {
            bail!("the API key is empty");
        }
        Ok(Immich { base, key })
    }

    // What this account says of each hash, in batches. The account is the
    // key's owner: a photo in someone else's account is missing here.
    fn lookup(&self, hashes: &[String], batch: usize, asked: &AtomicU64) -> Result<HashMap<String, Status>> {
        let mut found: HashMap<String, Status> = HashMap::new();
        let url = format!("{}/api/assets/bulk-upload-check", self.base);
        for chunk in hashes.chunks(batch.max(1)) {
            let body = CheckRequest {
                assets: chunk.iter().enumerate().map(|(i, h)| CheckItem { id: i.to_string(), checksum: h }).collect(),
            };
            let mut resp = ureq::post(&url)
                .header("x-api-key", &self.key)
                .send_json(&body)
                .map_err(|e| api_error(&url, e, "asset.upload"))?;
            let answer: CheckResponse = resp.body_mut().read_json().context("Immich's answer")?;
            for r in answer.results {
                let Some(h) = r.id.parse::<usize>().ok().and_then(|i| chunk.get(i)) else { continue };
                let status = match (r.action.as_str(), r.reason.as_deref()) {
                    ("reject", Some("duplicate")) if r.is_trashed == Some(true) => Status::Trashed,
                    ("reject", Some("duplicate")) => Status::Present,
                    ("accept", _) => Status::Missing,
                    (a, reason) => Status::Error(format!("Immich: {a} {}", reason.unwrap_or(""))),
                };
                found.insert(h.clone(), status);
            }
            asked.fetch_add(1, Ordering::Relaxed);
        }
        Ok(found)
    }
}

// ------------------------------------------------------------------ overlap

#[derive(Deserialize)]
struct Me {
    id: String,
    name: String,
    #[serde(default, rename = "isAdmin")]
    is_admin: bool,
}

#[derive(Deserialize)]
struct SearchResponse {
    assets: SearchPage,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct SearchPage {
    items: Vec<Asset>,
    next_cursor: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Asset {
    id: String,
    owner_id: String,
    // Base64 SHA-1.
    checksum: String,
    original_file_name: String,
    file_created_at: Option<String>,
    exif_info: Option<Exif>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Exif {
    file_size_in_byte: Option<u64>,
}

struct Account {
    login: String,
    id: String,
    name: String,
    bytes: u64,
    // SHA-1 (hex) → the account's assets with it.
    assets: HashMap<String, Vec<Asset>>,
}

impl Immich {
    fn me(&self) -> Result<Me> {
        let url = format!("{}/api/users/me", self.base);
        let mut resp = ureq::get(&url).header("x-api-key", &self.key).call().map_err(|e| api_error(&url, e, "user.read"))?;
        Ok(resp.body_mut().read_json()?)
    }

    // Every asset the key's user owns, 1000 a time. Immich's search leaves out
    // the trash and the hidden halves of Live Photos; with Partner Sharing it
    // also returns the partner's photos, hence the owner filter.
    fn owned_assets(&self, owner: &str, login: &str) -> Result<Vec<Asset>> {
        let url = format!("{}/api/search/metadata", self.base);
        let mut assets = Vec::new();
        let mut cursor: Option<String> = None;
        loop {
            let mut body = serde_json::json!({ "size": 1000, "filter": {}, "withExif": true });
            if let Some(c) = &cursor {
                body["cursor"] = serde_json::Value::String(c.clone());
            }
            let mut resp = ureq::post(&url).header("x-api-key", &self.key).send_json(&body).map_err(|e| api_error(&url, e, "asset.read"))?;
            let page: SearchResponse = resp.body_mut().with_config().limit(256 << 20).read_json().context("Immich's answer")?;
            assets.extend(page.assets.items.into_iter().filter(|a| a.owner_id == owner));
            if io::stderr().is_terminal() {
                eprint!("\r{login}: {} assets   ", assets.len());
            }
            match page.assets.next_cursor {
                Some(c) => cursor = Some(c),
                None => break,
            }
        }
        if io::stderr().is_terminal() {
            eprint!("\r{:40}\r", "");
        }
        Ok(assets)
    }
}

fn account(base: &str, who: &str) -> Result<Account> {
    // A path to a key file, else a login.
    let key = if Path::new(who).is_file() { read_key(Path::new(who))? } else { person_key(who)? };
    let immich = Immich::new(base.to_string(), key)?;
    let me = immich.me()?;
    let mut assets: HashMap<String, Vec<Asset>> = HashMap::new();
    for a in immich.owned_assets(&me.id, who)? {
        let sha1 = base64_to_hex(&a.checksum).with_context(|| format!("checksum of {}", a.original_file_name))?;
        assets.entry(sha1).or_default().push(a);
    }
    let login = Path::new(who).file_name().map(|n| n.to_string_lossy().trim_start_matches("token.").to_string()).unwrap_or_else(|| who.to_string());
    let bytes = assets.values().map(|xs| xs[0].exif_info.as_ref().and_then(|e| e.file_size_in_byte).unwrap_or(0)).sum();
    Ok(Account { login, id: me.id, name: me.name, bytes, assets })
}

fn overlap(args: OverlapArgs) -> Result<i32> {
    let base = server(&args.server)?;
    let out = args.out.clone().unwrap_or_else(|| PathBuf::from(format!("immich-overlap-{}", now_stamp())));
    if out.join("overlap.tsv").exists() {
        bail!("{} already holds an overlap.tsv: pick another --out", out.display());
    }
    let started = std::time::Instant::now();
    let a = account(&base, &args.accounts[0])?;
    let b = account(&base, &args.accounts[1])?;
    if a.id == b.id {
        bail!("both keys belong to the same account ({})", a.name);
    }

    let mut both: Vec<(&String, &Vec<Asset>, &Vec<Asset>)> =
        a.assets.iter().filter_map(|(h, xs)| b.assets.get(h).map(|ys| (h, xs, ys))).collect();
    // Oldest first: the order a person goes through them.
    both.sort_by(|x, y| x.1[0].file_created_at.cmp(&y.1[0].file_created_at));

    fs::create_dir_all(&out)?;
    let path = out.join("overlap.tsv");
    let mut f = io::BufWriter::new(File::create(&path)?);
    let link = |id: &str| format!("{base}/photos/{id}");
    writeln!(f, "sha1\tsize\ttaken\t{0}_file\t{0}_link\t{1}_file\t{1}_link", a.login, b.login)?;
    let mut bytes = 0u64;
    for (h, xs, ys) in &both {
        let size = xs[0].exif_info.as_ref().and_then(|e| e.file_size_in_byte).unwrap_or(0);
        bytes += size;
        writeln!(
            f,
            "{h}\t{size}\t{}\t{}\t{}\t{}\t{}",
            xs[0].file_created_at.as_deref().unwrap_or(""),
            xs[0].original_file_name.replace(['\t', '\n'], " "),
            link(&xs[0].id),
            ys[0].original_file_name.replace(['\t', '\n'], " "),
            link(&ys[0].id)
        )?;
    }
    f.flush()?;

    let count = |acc: &Account| acc.assets.values().map(Vec::len).sum::<usize>() as u64;
    // "< 1 %" rather than a misleading 0 for a few photos among thousands.
    let pct = |n: usize, of: u64| match if of > 0 { n as f64 * 100.0 / of as f64 } else { 0.0 } {
        p if p > 0.0 && p < 1.0 => "< 1 %".to_string(),
        p => format!("{p:.0} %"),
    };
    let w = a.login.len().max(b.login.len()).max(7) + 2;
    for acc in [&a, &b] {
        eprintln!("{:<w$}{} photos and videos, {} ({})", acc.login, num(count(acc)), size(acc.bytes), acc.name);
    }
    eprintln!("{:<w$}{}: {} of {}'s, {} of {}'s; {} stored twice", "in both", num(both.len() as u64),
        pct(both.len(), count(&a)), a.login, pct(both.len(), count(&b)), b.login, size(bytes));
    eprintln!("{:<w$}{}", "took", secs(started.elapsed()));
    eprintln!("list: {} (each line links to the photo in both accounts)", path.display());
    Ok(0)
}

fn base64_to_hex(s: &str) -> Result<String> {
    use base64::Engine;
    Ok(hex::encode(base64::engine::general_purpose::STANDARD.decode(s)?))
}

fn read_key(path: &Path) -> Result<String> {
    Ok(fs::read_to_string(path).with_context(|| format!("reading the API key from {}", path.display()))?.trim().to_string())
}

// ------------------------------------------------------------------- output

fn write_report(path: &Path, sources: &[Source], items: &[Item]) -> Result<()> {
    let mut f = io::BufWriter::new(File::create(path)?);
    writeln!(f, "status\tsha1\tsize\tsource\tpath\tdetail\tin")?;
    for it in items {
        let detail = if let Status::Error(e) = &it.status { e.as_str() } else { "" };
        let clean = |s: &str| s.replace(['\t', '\n'], " ");
        writeln!(
            f,
            "{}\t{}\t{}\t{}\t{}\t{}\t{}",
            it.status.label(),
            it.sha1.as_deref().unwrap_or(""),
            it.size,
            clean(&sources[it.src].name),
            clean(&it.path),
            clean(detail),
            it.holders
        )?;
    }
    Ok(())
}

fn summary(sources: &[Source], items: &[Item]) {
    let order = ["present", "missing", "trashed", "error", "unchecked", "empty", "not-media", "ignored"];
    for (src, s) in sources.iter().enumerate() {
        let mut count: HashMap<&str, (u64, u64)> = HashMap::new();
        for it in items.iter().filter(|it| it.src == src) {
            let c = count.entry(it.status.label()).or_default();
            c.0 += 1;
            c.1 += it.size;
        }
        let media: u64 = ["present", "missing", "trashed", "error", "unchecked", "empty"].iter().filter_map(|k| count.get(k)).map(|c| c.0).sum();
        eprintln!("{}: {} photos and videos", s.name, media);
        for k in order {
            if let Some((n, bytes)) = count.get(k) {
                eprintln!("  {k:<10} {n:>8}  {:>9}", size(*bytes));
            }
        }
    }
    let n = |st: &str| items.iter().filter(|it| it.status.label() == st).count();
    let bytes = |st: &str| items.iter().filter(|it| it.status.label() == st).map(|it| it.size).sum::<u64>();
    eprintln!();
    if n("missing") + n("trashed") + n("error") + n("unchecked") == 0 {
        eprintln!("Every photo and video is in Immich (\"not-media\" and \"ignored\" files never are: check them before deleting).");
    } else {
        if n("missing") > 0 {
            eprintln!("{} not in Immich ({}).", n("missing"), size(bytes("missing")));
        }
        if n("trashed") > 0 {
            eprintln!("{} only in Immich's trash: restore them there (Trash), or they go when it is emptied.", n("trashed"));
        }
        if n("error") > 0 {
            eprintln!("{} could not be read or checked: \"error\" lines in the report.", n("error"));
        }
        if n("unchecked") > 0 {
            eprintln!("{} hashed but not checked (--offline).", n("unchecked"));
        }
    }
}

// Missing files, plus every .json in the same folders (Takeout's metadata
// for them and the album's), under missing/<source>/<path>.
// From a folder on the same disk, a hard link rather than a copy: instant,
// and no space used (the files are only read, by immich-go too).
// Returns what it copied: (photos and videos, their bytes, .json files, how
// many of those are hard links).
fn extract(sources: &[Source], items: &[Item], dir: &Path) -> Result<(u64, u64, u64, u64)> {
    let mut folders: HashSet<(usize, String)> = HashSet::new();
    for it in items.iter().filter(|it| it.status == Status::Missing) {
        folders.insert((it.src, parent(&it.path).to_string()));
    }
    let wanted = |it: &Item| {
        it.status == Status::Missing || (it.kind == Kind::Json && folders.contains(&(it.src, parent(&it.path).to_string())))
    };
    // Counted as written: (photos and videos, their bytes, .json).
    let mut copied = (0u64, 0u64, 0u64);
    let mut linked = 0u64;
    let done = Arc::new(AtomicU64::new(0));
    let todo: u64 = items.iter().filter(|it| wanted(it)).map(|it| it.size).sum();
    let d = done.clone();
    let progress = Progress::start(move |t| {
        let d = d.load(Ordering::Relaxed);
        format!("copying {} / {}{}", size(d), size(todo), rate_eta(d, Some(todo), t))
    });
    let mut count = |it: &Item| {
        done.fetch_add(it.size, Ordering::Relaxed);
        if it.kind == Kind::Media {
            copied.0 += 1;
            copied.1 += it.size;
        } else {
            copied.2 += 1;
        }
    };
    for (src, s) in sources.iter().enumerate() {
        let mine: Vec<&Item> = items.iter().filter(|it| it.src == src && wanted(it)).collect();
        if mine.is_empty() {
            continue;
        }
        let root = dir.join(&s.name);
        match s.kind {
            SourceKind::Folder => {
                for it in &mine {
                    let Some(to) = target(&root, &it.path)? else { continue };
                    let from = it.file.as_ref().expect("folder items have a file");
                    if fs::hard_link(from, &to).is_ok() {
                        linked += 1;
                    } else {
                        fs::copy(from, &to)?;
                        keep_mtime(&to, it.mtime);
                    }
                    count(it);
                }
            }
            SourceKind::Zip => {
                let mut zip = zip::ZipArchive::new(File::open(&s.path)?)?;
                for it in &mine {
                    let Some(to) = target(&root, &it.path)? else { continue };
                    io::copy(&mut zip.by_index(it.zip_index)?, &mut File::create(&to)?)?;
                    count(it);
                }
            }
            SourceKind::Tar { gzip } => {
                let paths: HashMap<&str, &Item> = mine.iter().map(|it| (it.path.as_str(), *it)).collect();
                let mut tar = tar_reader(s, gzip)?;
                for entry in tar.entries()? {
                    let mut entry = entry?;
                    let path = entry.path()?.to_string_lossy().into_owned();
                    let Some(it) = paths.get(path.as_str()) else { continue };
                    if entry.header().entry_type().is_file() {
                        let Some(to) = target(&root, &path)? else { continue };
                        io::copy(&mut entry, &mut File::create(&to)?)?;
                        let mtime = entry.header().mtime().ok().map(|t| UNIX_EPOCH + Duration::from_secs(t));
                        keep_mtime(&to, mtime);
                        count(it);
                    }
                }
            }
        }
    }
    progress.stop();
    eprintln!("copied {} files (the missing ones and their .json) to {}", copied.0 + copied.2, dir.display());
    Ok((copied.0, copied.1, copied.2, linked))
}

fn parent(path: &str) -> &str {
    path.rsplit_once('/').map(|(p, _)| p).unwrap_or("")
}

// <root>/<path>, or None (with a warning) for an archive entry that would
// land outside it ("../x", "/etc/x").
fn target(root: &Path, path: &str) -> Result<Option<PathBuf>> {
    let rel = Path::new(path);
    if rel.components().any(|c| !matches!(c, Component::Normal(_) | Component::CurDir)) {
        eprintln!("not extracting {path:?}: it points outside the folder");
        return Ok(None);
    }
    let to = root.join(rel);
    fs::create_dir_all(to.parent().expect("a file has a folder"))?;
    Ok(Some(to))
}

// The file's date is immich-go's last resort for a photo without one inside.
fn keep_mtime(path: &Path, mtime: Option<SystemTime>) {
    if let (Some(t), Ok(f)) = (mtime, File::options().write(true).open(path)) {
        let _ = f.set_modified(t);
    }
}

// What extract() copied, by immich-go mode: a Takeout, zipped or unpacked,
// goes through its Google Photos mode, which reads the .json next to each
// photo; the rest is plain folders.
fn upload_dirs(sources: &[Source], items: &[Item], dir: &Path) -> [(&'static str, Vec<PathBuf>); 2] {
    let (mut takeouts, mut folders) = (Vec::new(), Vec::new());
    for (src, s) in sources.iter().enumerate() {
        if !items.iter().any(|it| it.src == src && it.status == Status::Missing) || !dir.join(&s.name).exists() {
            continue;
        }
        if is_takeout(items, src) {
            takeouts.push(dir.join(&s.name));
        } else {
            folders.push(dir.join(&s.name));
        }
    }
    [("from-google-photos", takeouts), ("from-folder", folders)]
}

fn upload_hint(sources: &[Source], items: &[Item], dir: &Path, args: &Args, to: &Who) {
    let server = args.server.clone().or_else(|| std::env::var("IMMICH_SERVER").ok()).unwrap_or_else(|| "<server>".into());
    let key = &to.key_file;
    eprintln!("upload them to {}'s account with immich-go (dry run first, then the same without --dry-run), or run this again with --upload:", to.label);
    for (mode, paths) in upload_dirs(sources, items, dir) {
        if !paths.is_empty() {
            let paths: Vec<String> = paths.iter().map(|p| format!("\"{}\"", p.display())).collect();
            eprintln!("  immich-go upload {mode} --server {server} --api-key \"$(cat {key})\" --dry-run {}", paths.join(" "));
        }
    }
}

// $IMMICH_GO, else immich-go on the PATH, else ~/immich-go/immich-go (where
// scripts/immich-import.sh downloads it). Looked for before any hashing.
fn find_immich_go() -> Result<PathBuf> {
    if let Some(p) = std::env::var_os("IMMICH_GO").filter(|p| !p.is_empty()) {
        return Ok(PathBuf::from(p));
    }
    let on_path = std::env::var_os("PATH").into_iter().flat_map(|p| std::env::split_paths(&p).collect::<Vec<_>>()).map(|d| d.join("immich-go"));
    let home = std::env::var_os("HOME").map(|h| PathBuf::from(h).join("immich-go").join("immich-go"));
    on_path.chain(home).find(|p| p.is_file()).context(
        "--upload needs immich-go: put it on the PATH (https://github.com/simulot/immich-go/releases), or set IMMICH_GO",
    )
}

// Removes the file when dropped: the keys' config, whatever happens.
struct Remove(PathBuf);

impl Drop for Remove {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

// Sends what extract() copied to the first account with immich-go, as
// scripts/immich-import.sh does (same tag, errors don't stop the rest), then
// asks Immich again about what was missing. Returns (now present, still
// missing), or None for a dry run.
fn upload(bin: &Path, sources: &[Source], items: &mut [Item], dir: &Path, out: &Path, args: &Args, accounts: &[Who]) -> Result<Option<(u64, u64)>> {
    use std::os::unix::fs::OpenOptionsExt;
    let to = &accounts[0];
    // Pausing Immich's jobs takes an admin's key: the account's own, or any
    // other --person's. None: Immich keeps working during the upload.
    let admin = accounts.iter().find(|w| w.immich.me().is_ok_and(|m| m.is_admin));
    let config = out.join("immich-go.yaml");
    let _remove = Remove(config.clone());
    let mut f = File::options().write(true).create_new(true).mode(0o600).open(&config).with_context(|| format!("creating {}", config.display()))?;
    writeln!(f, "upload:\n    server: {}\n    api-key: {}", to.immich.base, to.immich.key)?;
    if let Some(a) = admin {
        writeln!(f, "    admin-api-key: {}", a.immich.key)?;
    }
    drop(f);

    let today = now_stamp();
    let tag = format!("import/{}/{}-{}-{}", to.label, &today[..4], &today[4..6], &today[6..8]);
    eprintln!();
    eprintln!("{} to {}'s account with immich-go, tag {tag}{}", if args.dry_run { "dry run" } else { "uploading" }, to.label,
        if admin.is_none() { " (no admin key: Immich's jobs keep running)" } else { "" });
    for (mode, paths) in upload_dirs(sources, items, dir) {
        if paths.is_empty() {
            continue;
        }
        let log = out.join(format!("immich-go-{mode}{}.log", if args.dry_run { "-dry-run" } else { "" }));
        let mut cmd = process::Command::new(bin);
        cmd.args(["upload", mode, "--no-ui", "--on-errors", "continue"])
            .arg("--config").arg(&config)
            .arg("--log-file").arg(&log)
            .args(["--tag", &tag]);
        if admin.is_none() {
            cmd.arg("--pause-immich-jobs=false");
        }
        if args.dry_run {
            cmd.arg("--dry-run");
        }
        cmd.args(&args.immich_go).args(&paths);
        // immich-go exits 1 when any file failed, even when it went on with
        // the rest: asking Immich afterwards tells what arrived.
        let status = cmd.status().with_context(|| format!("running {}", bin.display()))?;
        if !status.success() {
            eprintln!("immich-go {mode} ended with {status}: its log is {}", log.display());
        }
    }
    if args.dry_run {
        return Ok(None);
    }

    let sent: Vec<usize> = (0..items.len()).filter(|&i| items[i].status == Status::Missing).collect();
    for &i in &sent {
        items[i].status = Status::Unchecked;
        items[i].holders.clear();
    }
    check(accounts, items, args.batch)?;
    let count = |st: Status| sent.iter().filter(|&&i| items[i].status == st).count() as u64;
    Ok(Some((count(Status::Present), count(Status::Missing))))
}

// Google Takeout, whatever its folder names (they are translated): a photo
// with its metadata next to it, <name>.json or <name>.supplemental-metadata.json
// (Google shortens long names: <name>.supplemen.json, <name>.suppl.json...).
fn is_takeout(items: &[Item], src: usize) -> bool {
    let media: HashSet<&str> = items.iter().filter(|it| it.src == src && it.kind == Kind::Media).map(|it| it.path.as_str()).collect();
    items.iter().filter(|it| it.src == src && it.kind == Kind::Json).any(|it| {
        let base = &it.path[..it.path.len() - ".json".len()];
        media.contains(base) || base.rsplit_once('.').is_some_and(|(b, ext)| "supplemental-metadata".starts_with(ext) && media.contains(b))
    })
}

// 21 807: thousands apart, as a person reads them.
fn num(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(' ');
        }
        out.push(c);
    }
    out
}

fn secs(d: Duration) -> String {
    let t = d.as_secs_f64();
    if t < 60.0 { format!("{t:.1} s") } else if t < 3600.0 { format!("{} min {} s", t as u64 / 60, t as u64 % 60) } else { format!("{} h {} min", t as u64 / 3600, t as u64 % 3600 / 60) }
}

fn size(bytes: u64) -> String {
    let units = ["B", "KB", "MB", "GB", "TB"];
    let mut v = bytes as f64;
    let mut u = 0;
    while v >= 1000.0 && u < units.len() - 1 {
        v /= 1000.0;
        u += 1;
    }
    if u == 0 { format!("{bytes} B") } else { format!("{v:.1} {}", units[u]) }
}

// YYYYmmdd-HHMMSS for the default --out and the upload tag: local time, as
// immich-go's log, from date(1) (the static binary has no time zones); UTC
// without it.
fn now_stamp() -> String {
    if let Ok(o) = process::Command::new("date").arg("+%Y%m%d-%H%M%S").output() {
        let s = String::from_utf8_lossy(&o.stdout).trim().to_string();
        if o.status.success() && s.len() == 15 {
            return s;
        }
    }
    let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let (days, rem) = (secs / 86400, secs % 86400);
    // Howard Hinnant's days-to-civil.
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = yoe + era * 400 + i64::from(m <= 2);
    format!("{y:04}{m:02}{d:02}-{:02}{:02}{:02}", rem / 3600, rem % 3600 / 60, rem % 60)
}
