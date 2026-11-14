//! Where the browser's pictures of pages go: `TEENY_BROWSER_PICTURES`
//! (`bin/spine` sets it to `state/browser/screenshots/`).
//!
//! The core's `browser` tool takes a picture of the page after it opens one,
//! after a click, key press, select or check, and before it closes, but only
//! while a host has a sink registered (`__host::browser_pictures`), and it
//! never takes one of a page that shows a password the keychain typed. This
//! sink writes each picture as `<unix ms>-<n>.png` (or `.jpeg`) into the
//! folder, whole or not at all, and keeps the newest [`MAX_KEPT`].
//! `hands/tasks` moves the ones a task's run took into
//! `state/media/tasks/<id>/` when its report is settled, where a post's
//! `shots: <id>` finds them.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

use openhuman_embed::__host::browser_pictures::{register_browser_picture_sink, BrowserPicture};

/// The setting that names the folder; unset, no picture is taken at all.
pub const FOLDER_ENV: &str = "TEENY_BROWSER_PICTURES";
/// The most pictures the folder keeps; the oldest go first, so a folder no
/// one collects stays small.
pub const MAX_KEPT: usize = 24;

/// Registers the sink when `TEENY_BROWSER_PICTURES` names an absolute folder.
pub fn install() {
    let Some(folder) = folder(std::env::var(FOLDER_ENV).ok().as_deref()) else {
        return;
    };
    register_browser_picture_sink("teeny-browser-pictures", move |picture: &BrowserPicture| {
        save(&folder, picture)
            .map(|_| ())
            .map_err(|e| e.to_string())
    });
}

/// The folder a setting names, when it is a non-empty absolute path.
pub fn folder(setting: Option<&str>) -> Option<PathBuf> {
    let raw = setting?.trim();
    let path = Path::new(raw);
    (!raw.is_empty() && path.is_absolute()).then(|| path.to_path_buf())
}

/// A picture's file name: the time it was taken, so names sort in that
/// order, then a counter that keeps two in one millisecond apart.
pub fn file_name(taken_at_ms: u128, n: u64, media_type: &str) -> String {
    let extension = if media_type == "image/jpeg" {
        "jpeg"
    } else {
        "png"
    };
    format!("{taken_at_ms:013}-{n:06}.{extension}")
}

/// Writes `picture` into `dir` (beside it under a dot name first, then
/// renamed, so a reader never sees half an image) and prunes the folder.
pub fn save(dir: &Path, picture: &BrowserPicture) -> std::io::Result<PathBuf> {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    std::fs::create_dir_all(dir)?;
    let name = file_name(
        picture.taken_at_ms,
        COUNTER.fetch_add(1, Ordering::Relaxed) % 1_000_000,
        picture.media_type,
    );
    let path = dir.join(&name);
    let partial = dir.join(format!(".{name}.part"));
    std::fs::write(&partial, &picture.bytes)?;
    std::fs::rename(&partial, &path)?;
    prune(dir, MAX_KEPT);
    Ok(path)
}

/// Drops the oldest pictures (by name) so that at most `keep` remain.
pub fn prune(dir: &Path, keep: usize) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    let mut pictures: Vec<PathBuf> = entries
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            let name = path.file_name().and_then(|n| n.to_str()).unwrap_or(".");
            path.is_file()
                && !name.starts_with('.')
                && (name.ends_with(".png") || name.ends_with(".jpeg"))
        })
        .collect();
    if pictures.len() <= keep {
        return;
    }
    pictures.sort();
    for old in &pictures[..pictures.len() - keep] {
        let _ = std::fs::remove_file(old);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("teeny-pictures-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    fn picture(at: u128, media_type: &'static str) -> BrowserPicture {
        BrowserPicture {
            bytes: b"\x89PNG\r\n\x1a\nxx".to_vec(),
            media_type,
            after: "open".into(),
            taken_at_ms: at,
        }
    }

    #[test]
    fn only_an_absolute_folder_turns_pictures_on() {
        assert_eq!(folder(None), None);
        assert_eq!(folder(Some(" ")), None);
        assert_eq!(folder(Some("state/browser/screenshots")), None);
        assert_eq!(
            folder(Some("/srv/state/browser/screenshots")),
            Some(PathBuf::from("/srv/state/browser/screenshots"))
        );
    }

    #[test]
    fn a_picture_is_saved_whole_under_a_name_that_sorts_by_time() {
        let dir = scratch("save");
        let first = save(&dir, &picture(1_789_369_260_000, "image/png")).unwrap();
        let second = save(&dir, &picture(1_789_369_320_000, "image/jpeg")).unwrap();
        assert!(first.file_name() < second.file_name());
        assert!(second.to_string_lossy().ends_with(".jpeg"));
        assert_eq!(std::fs::read(&first).unwrap(), b"\x89PNG\r\n\x1a\nxx");
        let leftovers = std::fs::read_dir(&dir).unwrap().filter(|e| {
            e.as_ref()
                .unwrap()
                .file_name()
                .to_string_lossy()
                .ends_with(".part")
        });
        assert_eq!(leftovers.count(), 0);
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_folder_keeps_only_the_newest() {
        let dir = scratch("prune");
        std::fs::create_dir_all(&dir).unwrap();
        for n in 0..(MAX_KEPT as u128 + 5) {
            std::fs::write(dir.join(file_name(1_000 + n, 0, "image/png")), b"x").unwrap();
        }
        std::fs::write(dir.join("notes.txt"), b"kept").unwrap();
        prune(&dir, MAX_KEPT);
        let mut names: Vec<String> = std::fs::read_dir(&dir)
            .unwrap()
            .map(|e| e.unwrap().file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".png"))
            .collect();
        names.sort();
        assert_eq!(names.len(), MAX_KEPT);
        assert_eq!(names[0], file_name(1_005, 0, "image/png"));
        assert!(dir.join("notes.txt").exists());
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
