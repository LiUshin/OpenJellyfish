//! The signed application is a read-only seed. Never run the backend in Resources.
use std::fs;
use std::io;
use std::os::unix::fs::symlink;
use std::path::{Path, PathBuf};

const PAYLOAD: &[&str] = &["app", "frontend", "python", "node", "launcher.py", "requirements.txt"];
const STATE: &[&str] = &["config", "users", "data", "logs", ".env"];

fn copy_tree(src: &Path, dst: &Path) -> io::Result<()> {
    let metadata = fs::symlink_metadata(src)?;
    if metadata.file_type().is_symlink() {
        // Preserve links inside embedded runtimes rather than modifying their targets.
        symlink(fs::read_link(src)?, dst)
    } else if metadata.is_dir() {
        fs::create_dir_all(dst)?;
        for entry in fs::read_dir(src)? {
            let entry = entry?;
            copy_tree(&entry.path(), &dst.join(entry.file_name()))?;
        }
        Ok(())
    } else {
        fs::copy(src, dst).map(|_| ())
    }
}

/// Caller holds an OS file lock across this operation. Renames publish only
/// complete trees; a failed copy is retried without deleting existing user data.
pub fn prepare(resources: &Path, home: &Path) -> io::Result<PathBuf> {
    let id = fs::read_to_string(resources.join("payload-id.txt"))?;
    let id = id.trim();
    if id.len() != 64 || !id.bytes().all(|b| b.is_ascii_hexdigit()) {
        return Err(io::Error::new(io::ErrorKind::InvalidData, "Invalid runtime payload ID"));
    }
    fs::create_dir_all(home)?;
    let shared = home.join("shared");
    if !shared.exists() {
        let staging = home.join("shared.partial");
        if staging.exists() { fs::remove_dir_all(&staging)?; }
        fs::create_dir(&staging)?;
        for name in STATE {
            let source = resources.join(name);
            let destination = staging.join(name);
            if source.exists() {
                // Also migrates state from an older app if it is still in this bundle.
                copy_tree(&source, &destination)?;
            } else if *name == ".env" {
                fs::write(destination, b"")?;
            } else {
                fs::create_dir(destination)?;
            }
        }
        fs::rename(staging, &shared)?;
    }
    let runtimes = home.join("runtimes");
    fs::create_dir_all(&runtimes)?;
    let runtime = runtimes.join(id);
    if !runtime.exists() {
        let staging = runtimes.join(format!("{id}.partial"));
        if staging.exists() { fs::remove_dir_all(&staging)?; }
        fs::create_dir(&staging)?;
        for name in PAYLOAD {
            copy_tree(&resources.join(name), &staging.join(name))?;
        }
        for name in STATE {
            symlink(shared.join(name), staging.join(name))?;
        }
        fs::rename(staging, &runtime)?;
    }
    Ok(runtime)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn upgrades_preserve_state_and_bundle_and_retry_partial_copy() {
        let root = std::env::temp_dir().join(format!("ojf-workspace-test-{}", std::process::id()));
        if root.exists() { fs::remove_dir_all(&root).unwrap(); }
        let resources = root.join("Example.app/Contents/Resources");
        let home = root.join("Application Support");
        fs::create_dir_all(&resources).unwrap();
        for name in PAYLOAD {
            fs::write(resources.join(name), b"version one").unwrap();
        }
        fs::create_dir(resources.join("config")).unwrap();
        fs::write(resources.join("config/registration_keys.json"), b"seed").unwrap();
        fs::write(resources.join("payload-id.txt"), "a".repeat(64)).unwrap();
        let first = prepare(&resources, &home).unwrap();
        fs::write(first.join(".env"), b"user settings").unwrap();
        fs::write(first.join("data/checkpoints.db"), b"user data").unwrap();
        fs::write(first.join("config/registration_keys.json"), b"user keys").unwrap();
        assert!(!resources.join(".env").exists());
        assert_eq!(fs::read(resources.join("config/registration_keys.json")).unwrap(), b"seed");
        assert_eq!(prepare(&resources, &home).unwrap(), first);
        fs::write(resources.join("payload-id.txt"), "b".repeat(64)).unwrap();
        fs::remove_file(resources.join("node")).unwrap();
        assert!(prepare(&resources, &home).is_err());
        assert!(!home.join("runtimes").join("b".repeat(64)).exists());
        fs::write(resources.join("node"), b"version two").unwrap();
        let second = prepare(&resources, &home).unwrap();
        assert_ne!(first, second);
        assert_eq!(fs::read(second.join(".env")).unwrap(), b"user settings");
        assert_eq!(fs::read(second.join("data/checkpoints.db")).unwrap(), b"user data");
        assert_eq!(fs::read(second.join("config/registration_keys.json")).unwrap(), b"user keys");
        assert_eq!(fs::read(first.join("node")).unwrap(), b"version one");
        assert_eq!(fs::read(second.join("node")).unwrap(), b"version two");
        fs::write(resources.join("payload-id.txt"), "../../escape").unwrap();
        assert!(prepare(&resources, &home).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
