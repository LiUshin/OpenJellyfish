use chrono::{Local, Utc};
use rand::Rng;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufWriter, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{Manager, RunEvent, State, WindowEvent};
use walkdir::WalkDir;
use zip::write::SimpleFileOptions;
use zip::ZipWriter;

// ── State ────────────────────────────────────────────────────────

struct AppState {
    jellyfish_process: Mutex<Option<Child>>,
    project_dir: Mutex<PathBuf>,
    backend_port: Mutex<u16>,
    frontend_port: Mutex<u16>,
}

// ── Types ────────────────────────────────────────────────────────

/// 配置中心管理的全部 .env 键（单一真相源）。
///
/// 新增可配置项只需在此追加键名 + 在 `dist/index.html` 的 `CONFIG_SCHEMA` 加一项，
/// 无需改 load/save 逻辑（二者均基于此泛型 map）。空字符串值 = 从 .env 删除该行。
const KNOWN_ENV_KEYS: &[&str] = &[
    // —— LLM Provider（key + base_url / region / group_id）——
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "BEDROCK_API_KEY",
    "BEDROCK_REGION",
    "KIMI_API_KEY",
    "KIMI_BASE_URL",
    "MINIMAX_API_KEY",
    "MINIMAX_GROUP_ID",
    "DOUBAO_ACCESS_KEY",
    "DOUBAO_SECRET_KEY",
    "DOUBAO_REGION",
    // —— 联网搜索 ——
    "TAVILY_API_KEY",
    "CLOUDSWAY_SEARCH_KEY",
    "CLOUDSWAY_SEARCH_URL",
    "CLOUDSWAY_READ_URL",
    // —— 多模态 per-capability 覆盖（留空回退 provider 默认）——
    "IMAGE_API_KEY",
    "IMAGE_BASE_URL",
    "TTS_API_KEY",
    "TTS_BASE_URL",
    "VIDEO_API_KEY",
    "VIDEO_BASE_URL",
    "S2S_API_KEY",
    "S2S_BASE_URL",
    "STT_API_KEY",
    "STT_BASE_URL",
    // —— 运维开关（高级）——
    "SAFE_STARTUP",
    "DISABLE_SCHEDULER",
    "SCHEDULER_MAX_CONCURRENT",
    "RESTORE_VENVS_ON_STARTUP",
    "RESTORE_VENVS_CONCURRENCY",
    "UVICORN_WORKERS",
    "SUPERADMIN_SCRIPT_UNRESTRICTED",
    "SCRIPT_CONCURRENCY",
    "SCRIPT_QUEUE_TIMEOUT",
    "STORAGE_BACKEND",
    // —— 全局默认时区 ——
    "DEFAULT_TZ_OFFSET_HOURS",
];

/// 泛型环境配置：键 = .env 变量名，值 = 字符串值。
type EnvMap = HashMap<String, String>;

#[derive(Serialize)]
struct EnvStatus {
    has_python: bool,
    python_path: String,
    python_version: String,
    python_bundled: bool,
    has_node: bool,
    node_path: String,
    node_version: String,
    node_bundled: bool,
    has_deps: bool,
    project_dir: String,
    first_run: bool,
}

#[derive(Serialize)]
struct TestResult {
    provider: String,
    ok: bool,
    message: String,
}

#[derive(Serialize)]
struct ProcessStatus {
    running: bool,
    backend_port: u16,
    frontend_port: u16,
    backend_ready: bool,
    frontend_ready: bool,
    local_ip: Option<String>,
}

// ── Superadmin Types ─────────────────────────────────────────────

#[derive(Serialize, Deserialize, Clone)]
struct RegKeyInfo {
    key: String,
    used: bool,
    created_at: Option<String>,
    used_by: Option<String>,
    used_at: Option<String>,
}

#[derive(Serialize, Deserialize)]
struct RegKeysFile {
    description: Option<String>,
    keys: Vec<RegKeyInfo>,
}

#[derive(Serialize)]
struct AdminUserInfo {
    user_id: String,
    username: String,
    created_at: String,
    last_login: String,
    reg_key: String,
    has_api_keys: bool,
    disabled: bool,
}

#[derive(Serialize)]
struct StorageBreakdown {
    name: String,
    bytes: u64,
}

#[derive(Serialize)]
struct AdminStorage {
    user_id: String,
    total_bytes: u64,
    breakdown: Vec<StorageBreakdown>,
}

#[derive(Serialize)]
struct AdminKeyStatus {
    user_id: String,
    providers: Vec<String>,
}

#[derive(Serialize)]
struct AdminStats {
    total_users: usize,
    active_7d: usize,
    keys_configured: usize,
    total_reg_keys: usize,
    used_reg_keys: usize,
    available_reg_keys: usize,
}

#[derive(Serialize)]
struct ResetPasswordResult {
    user_id: String,
    temp_password: String,
}

// ── Path Resolution ──────────────────────────────────────────────

/// Strip Windows `\\?\` extended-length path prefix.
///
/// Tauri's `resource_dir()` on Windows returns paths in the
/// `\\?\C:\Program Files\App` form. When this prefix reaches a Python
/// subprocess (via `Command::new(python_exe)` or `JELLYFISH_PYTHON` env),
/// `sys.executable` and every `__file__` in `site-packages` inherit it —
/// which then breaks `pycryptodome`'s `os.path.isfile()` check for native
/// `.pyd` modules (it cannot find `_cpuid_c.cp37-win_amd64.pyd` etc.,
/// even though the file is on disk).
///
/// This helper normalizes paths so all subprocesses see plain `D:\…` form.
fn strip_win_extended_prefix(path: &std::path::Path) -> PathBuf {
    if !cfg!(windows) {
        return path.to_path_buf();
    }
    let s = path.to_string_lossy();
    if let Some(rest) = s.strip_prefix(r"\\?\") {
        // UNC variant: \\?\UNC\server\share -> \\server\share
        if let Some(unc) = rest.strip_prefix("UNC\\") {
            return PathBuf::from(format!(r"\\{}", unc));
        }
        return PathBuf::from(rest);
    }
    path.to_path_buf()
}

fn find_project_dir_dev() -> PathBuf {
    let exe = std::env::current_exe().unwrap_or_default();
    let mut dir = exe.parent().unwrap_or(std::path::Path::new(".")).to_path_buf();
    for _ in 0..5 {
        if dir.join("app").join("main.py").exists() {
            return strip_win_extended_prefix(&dir);
        }
        if let Some(parent) = dir.parent() {
            dir = parent.to_path_buf();
        } else {
            break;
        }
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    strip_win_extended_prefix(&cwd)
}

fn resolve_project_dir(app: &tauri::AppHandle) -> PathBuf {
    if let Ok(res_dir) = app.path().resource_dir() {
        if res_dir.join("app").join("main.py").exists() {
            return strip_win_extended_prefix(&res_dir);
        }
    }
    find_project_dir_dev()
}

fn find_bundled_python(project_dir: &std::path::Path) -> Option<(PathBuf, bool)> {
    let python_dir = project_dir.join("python");
    if cfg!(windows) {
        let p = python_dir.join("python.exe");
        if p.exists() {
            return Some((strip_win_extended_prefix(&p), true));
        }
    } else {
        let p = python_dir.join("bin").join("python3");
        if p.exists() {
            return Some((p, true));
        }
    }
    find_system_exe(&["python3", "python"]).map(|p| (PathBuf::from(p), false))
}

fn find_bundled_node(project_dir: &std::path::Path) -> Option<(PathBuf, bool)> {
    let node_dir = project_dir.join("node");
    if cfg!(windows) {
        let p = node_dir.join("node.exe");
        if p.exists() {
            return Some((strip_win_extended_prefix(&p), true));
        }
    } else {
        let p = node_dir.join("bin").join("node");
        if p.exists() {
            return Some((p, true));
        }
    }
    find_system_exe(&["node"]).map(|p| (PathBuf::from(p), false))
}

fn find_system_exe(names: &[&str]) -> Option<String> {
    for name in names {
        let cmd = if cfg!(windows) {
            Command::new("where").arg(name).output()
        } else {
            Command::new("which").arg(name).output()
        };
        if let Ok(output) = cmd {
            if output.status.success() {
                let path = String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .next()
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if !path.is_empty() {
                    return Some(path);
                }
            }
        }
    }
    None
}

fn get_exe_version(exe_path: &str) -> String {
    Command::new(exe_path)
        .arg("--version")
        .output()
        .ok()
        .map(|o| {
            let out = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if out.is_empty() {
                String::from_utf8_lossy(&o.stderr).trim().to_string()
            } else {
                out
            }
        })
        .unwrap_or_default()
}

// ── Network Utilities ────────────────────────────────────────────

fn get_local_ip() -> Option<String> {
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect("8.8.8.8:80").ok()?;
    Some(socket.local_addr().ok()?.ip().to_string())
}

fn is_port_open(port: u16) -> bool {
    TcpStream::connect_timeout(
        &format!("127.0.0.1:{}", port).parse().unwrap(),
        Duration::from_secs(1),
    )
    .is_ok()
}

fn find_free_port(start: u16) -> u16 {
    for offset in 0..50 {
        let port = start + offset;
        if !is_port_open(port) {
            return port;
        }
    }
    start
}

// ── Existing Commands ────────────────────────────────────────────

#[tauri::command]
fn detect_environment(app: tauri::AppHandle, state: State<'_, AppState>) -> EnvStatus {
    let project_dir = resolve_project_dir(&app);
    *state.project_dir.lock().unwrap() = project_dir.clone();

    let (has_python, python_path, python_bundled) = match find_bundled_python(&project_dir) {
        Some((path, bundled)) => (true, path.to_string_lossy().to_string(), bundled),
        None => (false, String::new(), false),
    };
    let python_version = if has_python {
        get_exe_version(&python_path)
    } else {
        String::new()
    };

    let (has_node, node_path, node_bundled) = match find_bundled_node(&project_dir) {
        Some((path, bundled)) => (true, path.to_string_lossy().to_string(), bundled),
        None => (false, String::new(), false),
    };
    let node_version = if has_node {
        get_exe_version(&node_path)
    } else {
        String::new()
    };

    let has_deps = project_dir.join("app").join("main.py").exists();
    let first_run = !project_dir.join(".env").exists() && !project_dir.join("users").exists();

    EnvStatus {
        has_python,
        python_path,
        python_version,
        python_bundled,
        has_node,
        node_path,
        node_version,
        node_bundled,
        has_deps,
        project_dir: project_dir.to_string_lossy().to_string(),
        first_run,
    }
}

#[tauri::command]
fn load_env_config(state: State<'_, AppState>) -> EnvMap {
    let project_dir = state.project_dir.lock().unwrap();
    let env_path = project_dir.join(".env");

    let known: HashSet<&str> = KNOWN_ENV_KEYS.iter().copied().collect();
    let mut config: EnvMap = HashMap::new();

    if let Ok(content) = fs::read_to_string(&env_path) {
        for line in content.lines() {
            let line = line.trim();
            if line.starts_with('#') || !line.contains('=') {
                continue;
            }
            let mut parts = line.splitn(2, '=');
            let key = parts.next().unwrap_or("").trim();
            let val = parts.next().unwrap_or("").trim().to_string();
            if val.is_empty() || !known.contains(key) {
                continue;
            }
            config.insert(key.to_string(), val);
        }
    }

    config
}

#[tauri::command]
fn save_env_config(config: EnvMap, state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap();
    let env_path = project_dir.join(".env");

    let known: HashSet<&str> = KNOWN_ENV_KEYS.iter().copied().collect();
    let existing = fs::read_to_string(&env_path).unwrap_or_default();
    let mut lines: Vec<String> = Vec::new();
    let mut written_keys: HashSet<String> = HashSet::new();

    // 第一遍：保留注释/未知行；对已知键 upsert（空值 = 删除该行）。
    for line in existing.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') || !trimmed.contains('=') {
            lines.push(line.to_string());
            continue;
        }
        let key = trimmed.splitn(2, '=').next().unwrap_or("").trim();
        // 仅处理「已知 且 本次提交包含」的键，其余原样保留（防误删无关 env）。
        if known.contains(key) {
            if let Some(val) = config.get(key) {
                let v = val.trim();
                if !v.is_empty() {
                    lines.push(format!("{}={}", key, v));
                }
                // v 为空 → 不写入该行（即删除）
                written_keys.insert(key.to_string());
                continue;
            }
        }
        lines.push(line.to_string());
    }

    // 第二遍：追加文件里原本没有、但本次提交了非空值的已知键。
    for k in KNOWN_ENV_KEYS {
        if written_keys.contains(*k) {
            continue;
        }
        if let Some(val) = config.get(*k) {
            let v = val.trim();
            if !v.is_empty() {
                lines.push(format!("{}={}", k, v));
            }
        }
    }

    fs::write(&env_path, lines.join("\n") + "\n").map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
async fn test_api_key(provider: String, api_key: String, base_url: String) -> TestResult {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .unwrap();

    match provider.as_str() {
        "openai" => {
            let url = if base_url.is_empty() {
                "https://api.openai.com/v1".to_string()
            } else {
                base_url.trim_end_matches('/').to_string()
            };
            match client
                .get(format!("{}/models", url))
                .header("Authorization", format!("Bearer {}", api_key))
                .send()
                .await
            {
                Ok(resp) => TestResult {
                    provider: "openai".into(),
                    ok: resp.status().is_success(),
                    message: if resp.status().is_success() {
                        "连接成功 ✓".into()
                    } else {
                        format!("HTTP {}", resp.status())
                    },
                },
                Err(e) => TestResult {
                    provider: "openai".into(),
                    ok: false,
                    message: format!("连接失败: {}", e),
                },
            }
        }
        "anthropic" => {
            let url = if base_url.is_empty() {
                "https://api.anthropic.com".to_string()
            } else {
                base_url.trim_end_matches('/').to_string()
            };
            match client
                .get(format!("{}/v1/models", url))
                .header("x-api-key", &api_key)
                .header("anthropic-version", "2023-06-01")
                .send()
                .await
            {
                Ok(resp) => TestResult {
                    provider: "anthropic".into(),
                    ok: resp.status().is_success(),
                    message: if resp.status().is_success() {
                        "连接成功 ✓".into()
                    } else {
                        format!("HTTP {}", resp.status())
                    },
                },
                Err(e) => TestResult {
                    provider: "anthropic".into(),
                    ok: false,
                    message: format!("连接失败: {}", e),
                },
            }
        }
        "tavily" => {
            match client
                .post("https://api.tavily.com/search")
                .header("Authorization", format!("Bearer {}", api_key))
                .header("Content-Type", "application/json")
                .body(r#"{"query":"test","max_results":1,"search_depth":"basic"}"#)
                .send()
                .await
            {
                Ok(resp) => TestResult {
                    provider: "tavily".into(),
                    ok: resp.status().is_success(),
                    message: if resp.status().is_success() {
                        "连接成功 ✓".into()
                    } else {
                        format!("HTTP {}", resp.status())
                    },
                },
                Err(e) => TestResult {
                    provider: "tavily".into(),
                    ok: false,
                    message: format!("连接失败: {}", e),
                },
            }
        }
        _ => TestResult {
            provider,
            ok: false,
            message: "未知 provider".into(),
        },
    }
}

#[tauri::command]
fn start_jellyfish(
    _app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<ProcessStatus, String> {
    let mut proc_guard = state.jellyfish_process.lock().unwrap();
    if let Some(ref mut child) = *proc_guard {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() {
            return Err("OpenJellyfish 已在运行中".into());
        }
    }

    let project_dir = state.project_dir.lock().unwrap().clone();
    let launcher = project_dir.join("launcher.py");
    if !launcher.exists() {
        return Err("找不到 launcher.py".into());
    }

    let backend_port = find_free_port(8000);
    let frontend_port = find_free_port(3000);
    *state.backend_port.lock().unwrap() = backend_port;
    *state.frontend_port.lock().unwrap() = frontend_port;

    let (python_path, _) = find_bundled_python(&project_dir).ok_or("找不到 Python")?;

    let mut cmd = Command::new(&python_path);
    cmd.arg(launcher.to_string_lossy().to_string())
        .arg("--port")
        .arg(backend_port.to_string())
        .arg("--frontend-port")
        .arg(frontend_port.to_string())
        .arg("--skip-check")
        .current_dir(&project_dir);

    cmd.env(
        "JELLYFISH_PYTHON",
        python_path.to_string_lossy().to_string(),
    );
    if let Some((node_path, _)) = find_bundled_node(&project_dir) {
        cmd.env("JELLYFISH_NODE", node_path.to_string_lossy().to_string());
    }

    let child = cmd.spawn().map_err(|e| format!("启动失败: {}", e))?;
    *proc_guard = Some(child);

    Ok(ProcessStatus {
        running: true,
        backend_port,
        frontend_port,
        backend_ready: false,
        frontend_ready: false,
        local_ip: get_local_ip(),
    })
}

/// Terminate the launcher.py child **and its entire process tree**.
///
/// `launcher.py` itself spawns two grandchildren (uvicorn backend + express
/// frontend). On Windows, `Child::kill()` only signals the immediate child,
/// leaving the grandchildren as orphan processes that keep the ports busy
/// and never shut down. We use `taskkill /T /F /PID <pid>` to recursively
/// kill the whole tree. On Unix, `SIGTERM` followed by a short grace period
/// then `SIGKILL` propagates to children that share the process group (the
/// launcher's `cleanup` handler also forwards SIGTERM internally).
fn kill_process_tree(child: &mut Child) {
    let pid = child.id();
    #[cfg(unix)]
    {
        unsafe {
            // Kill the whole process group if launcher set one; otherwise
            // SIGTERM the launcher itself which cleans up its children.
            libc::kill(pid as i32, libc::SIGTERM);
        }
        // Give launcher.py a moment to gracefully shut down children.
        std::thread::sleep(Duration::from_millis(800));
        if child.try_wait().ok().flatten().is_none() {
            unsafe {
                libc::kill(pid as i32, libc::SIGKILL);
            }
        }
    }
    #[cfg(windows)]
    {
        // /T = also terminate child processes spawned by this PID
        // /F = force termination
        let _ = Command::new("taskkill")
            .args(["/T", "/F", "/PID", &pid.to_string()])
            .output();
        // Fallback in case taskkill is unavailable for some reason.
        let _ = child.kill();
    }
    let _ = child.wait();
}

/// Internal helper shared by the Tauri command and the window-close handler.
fn shutdown_jellyfish(state: &AppState) -> Result<(), String> {
    let mut proc_guard = state.jellyfish_process.lock().unwrap();
    if let Some(ref mut child) = *proc_guard {
        kill_process_tree(child);
        *proc_guard = None;
        Ok(())
    } else {
        Err("OpenJellyfish 未运行".into())
    }
}

#[tauri::command]
fn stop_jellyfish(state: State<'_, AppState>) -> Result<(), String> {
    shutdown_jellyfish(&state)
}

#[tauri::command]
fn get_status(state: State<'_, AppState>) -> ProcessStatus {
    let mut proc_guard = state.jellyfish_process.lock().unwrap();
    let backend_port = *state.backend_port.lock().unwrap();
    let frontend_port = *state.frontend_port.lock().unwrap();

    let running = if let Some(ref mut child) = *proc_guard {
        child.try_wait().ok().flatten().is_none()
    } else {
        false
    };

    ProcessStatus {
        running,
        backend_port,
        frontend_port,
        backend_ready: running && is_port_open(backend_port),
        frontend_ready: running && is_port_open(frontend_port),
        local_ip: if running { get_local_ip() } else { None },
    }
}

#[tauri::command]
fn get_lan_ip() -> Option<String> {
    get_local_ip()
}

#[tauri::command]
fn open_in_browser(state: State<'_, AppState>) -> Result<(), String> {
    let port = *state.frontend_port.lock().unwrap();
    let url = format!("http://localhost:{}", port);
    open::that(&url).map_err(|e| e.to_string())
}

// ── About / Tools Commands ───────────────────────────────────────

/// Open the project root directory (where `launcher.py`, `.env`, `users/` live).
#[tauri::command]
fn open_project_dir(state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    open::that(&project_dir).map_err(|e| e.to_string())
}

/// Open the `users/` directory; auto-creates it if missing so the OS file
/// explorer always opens cleanly even on a fresh install.
#[tauri::command]
fn open_users_dir(state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let users_dir = project_dir.join("users");
    if !users_dir.exists() {
        fs::create_dir_all(&users_dir).map_err(|e| e.to_string())?;
    }
    open::that(&users_dir).map_err(|e| e.to_string())
}

/// Open the `logs/` directory; created lazily on first call so users
/// who haven't started OpenJellyfish yet still get a usable folder.
#[tauri::command]
fn open_logs_dir(state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let logs_dir = project_dir.join("logs");
    if !logs_dir.exists() {
        fs::create_dir_all(&logs_dir).map_err(|e| e.to_string())?;
    }
    open::that(&logs_dir).map_err(|e| e.to_string())
}

// ── System Health (read-only) ────────────────────────────────────

#[derive(Serialize)]
struct CheckpointInfo {
    exists: bool,
    db_bytes: u64,
    wal_bytes: u64,
    shm_bytes: u64,
}

#[derive(Serialize)]
struct LogFileInfo {
    name: String,
    bytes: u64,
    modified: String,
}

#[derive(Serialize)]
struct SystemHealth {
    project_dir: String,
    backend_running: bool,
    frontend_running: bool,
    backend_port: u16,
    frontend_port: u16,
    checkpoints: CheckpointInfo,
    logs: Vec<LogFileInfo>,
}

#[derive(Serialize)]
struct DiskUsage {
    total_bytes: u64,
    breakdown: Vec<StorageBreakdown>,
}

fn file_len(path: &std::path::Path) -> u64 {
    fs::metadata(path).map(|m| m.len()).unwrap_or(0)
}

/// Fast read-only health snapshot: runtime ports, checkpoints.db sizes,
/// and a listing of `logs/` files. Does NOT walk large dirs (use
/// `get_disk_usage` for that).
#[tauri::command]
fn get_system_health(state: State<'_, AppState>) -> Result<SystemHealth, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let backend_port = *state.backend_port.lock().unwrap();
    let frontend_port = *state.frontend_port.lock().unwrap();

    // Checkpoints (WAL mode → db + -wal + -shm)
    let data_dir = project_dir.join("data");
    let db = data_dir.join("checkpoints.db");
    let checkpoints = CheckpointInfo {
        exists: db.exists(),
        db_bytes: file_len(&db),
        wal_bytes: file_len(&data_dir.join("checkpoints.db-wal")),
        shm_bytes: file_len(&data_dir.join("checkpoints.db-shm")),
    };

    // Logs listing (may not exist until launcher has run once)
    let mut logs: Vec<LogFileInfo> = Vec::new();
    let logs_dir = project_dir.join("logs");
    if logs_dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&logs_dir) {
            for entry in entries.flatten() {
                let p = entry.path();
                if !p.is_file() {
                    continue;
                }
                let meta = match entry.metadata() {
                    Ok(m) => m,
                    Err(_) => continue,
                };
                let modified = meta
                    .modified()
                    .ok()
                    .map(|t| {
                        let dt: chrono::DateTime<chrono::Local> = t.into();
                        dt.format("%Y-%m-%d %H:%M").to_string()
                    })
                    .unwrap_or_default();
                logs.push(LogFileInfo {
                    name: entry.file_name().to_string_lossy().to_string(),
                    bytes: meta.len(),
                    modified,
                });
            }
        }
    }
    logs.sort_by(|a, b| b.modified.cmp(&a.modified));

    Ok(SystemHealth {
        project_dir: project_dir.to_string_lossy().to_string(),
        backend_running: is_port_open(backend_port),
        frontend_running: is_port_open(frontend_port),
        backend_port,
        frontend_port,
        checkpoints,
        logs,
    })
}

/// On-demand: walk project_dir top-level entries and report disk usage,
/// descending by size. Slow (walks venv/.git/users), so triggered by an
/// explicit button rather than on page load.
#[tauri::command]
fn get_disk_usage(state: State<'_, AppState>) -> Result<DiskUsage, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let mut breakdown: Vec<StorageBreakdown> = Vec::new();
    let mut total: u64 = 0;
    for entry in fs::read_dir(&project_dir).map_err(|e| e.to_string())?.flatten() {
        let p = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let bytes = if p.is_dir() {
            dir_size_bytes(&p)
        } else {
            entry.metadata().map(|m| m.len()).unwrap_or(0)
        };
        total += bytes;
        breakdown.push(StorageBreakdown { name, bytes });
    }
    breakdown.sort_by(|a, b| b.bytes.cmp(&a.bytes));
    Ok(DiskUsage {
        total_bytes: total,
        breakdown,
    })
}

/// Read-only tail of a log file under `logs/`. Path-safety: `name` must be
/// a bare filename directly inside `logs/` (no separators / `..`).
#[tauri::command]
fn tail_log(name: String, lines: usize, state: State<'_, AppState>) -> Result<String, String> {
    // Reject anything that isn't a plain filename
    if name.is_empty()
        || name.contains('/')
        || name.contains('\\')
        || name.contains("..")
    {
        return Err("非法日志文件名".into());
    }
    let project_dir = state.project_dir.lock().unwrap().clone();
    let logs_dir = project_dir.join("logs");
    let target = logs_dir.join(&name);

    // Resolve & confirm the file lives directly under logs_dir
    let logs_canon = fs::canonicalize(&logs_dir).map_err(|_| "日志目录不存在".to_string())?;
    let target_canon = fs::canonicalize(&target).map_err(|_| "日志文件不存在".to_string())?;
    if target_canon.parent() != Some(logs_canon.as_path()) {
        return Err("非法日志路径".into());
    }
    if !target_canon.is_file() {
        return Err("不是文件".into());
    }

    let content = fs::read_to_string(&target_canon)
        .map_err(|e| format!("读取失败: {}", e))?;
    let n = lines.clamp(1, 5000);
    let all: Vec<&str> = content.lines().collect();
    let start = all.len().saturating_sub(n);
    Ok(all[start..].join("\n"))
}

/// Open the GitHub releases page in the user's default browser.
///
/// Points to the public mirror at `LiUshin/OpenJellyfish` (the private
/// development repo `LiUshin/semi-deep-agent` is not exposed here).
#[tauri::command]
fn open_release_page() -> Result<(), String> {
    const RELEASE_URL: &str = "https://github.com/LiUshin/OpenJellyfish/releases/latest";
    open::that(RELEASE_URL).map_err(|e| e.to_string())
}

/// Return the current launcher version (read from Cargo.toml at compile time).
#[tauri::command]
fn get_app_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

// ── Backup (one-click ZIP of config / data / logs / users) ──────

#[derive(Serialize)]
struct BackupResult {
    ok: bool,
    cancelled: bool,
    path: String,
    file_count: usize,
    size_bytes: u64,
    skipped_dirs: Vec<String>,
    message: String,
}

/// Folders to recursively skip even if they appear inside the four target
/// dirs — bytecode caches, virtualenvs, and node_modules are huge and never
/// part of a useful backup.
const BACKUP_SKIP_DIRS: &[&str] = &[
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "venv",
    ".venv",
    "target",
    ".git",
];

fn should_skip_path(rel: &Path) -> bool {
    rel.components().any(|c| {
        let name = c.as_os_str().to_string_lossy();
        BACKUP_SKIP_DIRS.iter().any(|s| *s == name)
    })
}

/// Stream a single file into the open ZIP archive.
fn zip_add_file<W: Write + std::io::Seek>(
    zw: &mut ZipWriter<W>,
    abs_path: &Path,
    archive_name: &str,
    options: SimpleFileOptions,
) -> std::io::Result<u64> {
    zw.start_file(archive_name, options)
        .map_err(|e| std::io::Error::other(e.to_string()))?;
    let mut f = File::open(abs_path)?;
    let mut buf = [0u8; 64 * 1024];
    let mut total: u64 = 0;
    loop {
        let n = f.read(&mut buf)?;
        if n == 0 {
            break;
        }
        zw.write_all(&buf[..n])?;
        total += n as u64;
    }
    Ok(total)
}

/// Pack `config/`, `data/`, `logs/`, `users/` of the current project into a
/// ZIP file. The user picks the destination via a native save dialog.
///
/// `.env` is intentionally excluded (contains plaintext API keys; if the
/// user wants to migrate keys they can re-enter them on the new machine).
#[tauri::command]
async fn pack_backup(state: State<'_, AppState>) -> Result<BackupResult, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();

    // Ask the user where to save.
    let default_name = format!(
        "openjellyfish-backup-{}.zip",
        Local::now().format("%Y%m%d-%H%M%S")
    );
    let chosen = rfd::AsyncFileDialog::new()
        .add_filter("ZIP archive", &["zip"])
        .set_file_name(&default_name)
        .set_title("保存 OpenJellyfish 备份到…")
        .save_file()
        .await;

    let Some(handle) = chosen else {
        return Ok(BackupResult {
            ok: false,
            cancelled: true,
            path: String::new(),
            file_count: 0,
            size_bytes: 0,
            skipped_dirs: vec![],
            message: "已取消".into(),
        });
    };

    let out_path: PathBuf = handle.path().to_path_buf();
    let out_path = strip_win_extended_prefix(&out_path);

    // Open ZIP for writing.
    let file = File::create(&out_path).map_err(|e| format!("无法创建 ZIP: {}", e))?;
    let mut zw = ZipWriter::new(BufWriter::new(file));
    let options = SimpleFileOptions::default()
        .compression_method(zip::CompressionMethod::Deflated)
        .compression_level(Some(6))
        .unix_permissions(0o644);

    let targets = ["config", "data", "logs", "users"];
    let mut file_count: usize = 0;
    let mut total_bytes: u64 = 0;
    let mut skipped: Vec<String> = Vec::new();

    for target in targets {
        let target_dir = project_dir.join(target);
        if !target_dir.exists() {
            skipped.push(format!("{} (不存在)", target));
            continue;
        }
        for entry in WalkDir::new(&target_dir)
            .follow_links(false)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            let abs = entry.path();
            // Compute path relative to project_dir so the archive layout
            // mirrors the on-disk tree.
            let rel = match abs.strip_prefix(&project_dir) {
                Ok(r) => r,
                Err(_) => continue,
            };
            if should_skip_path(rel) {
                continue;
            }
            // Use forward slashes inside the ZIP (cross-platform convention).
            let archive_name = rel.to_string_lossy().replace('\\', "/");
            if entry.file_type().is_dir() {
                // Empty dirs: store an explicit dir entry so structure round-trips.
                if archive_name.is_empty() {
                    continue;
                }
                let dir_entry = if archive_name.ends_with('/') {
                    archive_name
                } else {
                    format!("{}/", archive_name)
                };
                let _ = zw.add_directory(&dir_entry, options);
            } else if entry.file_type().is_file() {
                match zip_add_file(&mut zw, abs, &archive_name, options) {
                    Ok(n) => {
                        file_count += 1;
                        total_bytes += n;
                    }
                    Err(e) => {
                        // Don't abort the whole backup for one unreadable file
                        // (e.g. logs locked by uvicorn on Windows).
                        skipped.push(format!("{} ({})", archive_name, e));
                    }
                }
            }
        }
    }

    zw.finish()
        .map_err(|e| format!("ZIP 收尾失败: {}", e))?;

    let final_size = fs::metadata(&out_path).map(|m| m.len()).unwrap_or(total_bytes);

    Ok(BackupResult {
        ok: true,
        cancelled: false,
        path: out_path.to_string_lossy().to_string(),
        file_count,
        size_bytes: final_size,
        skipped_dirs: skipped,
        message: format!("已备份 {} 个文件", file_count),
    })
}

#[tauri::command]
fn install_pip_deps(state: State<'_, AppState>) -> Result<String, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let (python_path, _) = find_bundled_python(&project_dir).ok_or("找不到 Python")?;
    let req = project_dir.join("requirements.txt");
    if !req.exists() {
        return Err("找不到 requirements.txt".into());
    }

    let output = Command::new(python_path.to_string_lossy().to_string())
        .args(["-m", "pip", "install", "-r"])
        .arg(req.to_string_lossy().to_string())
        .args(["--no-warn-script-location", "-q"])
        .current_dir(&project_dir)
        .output()
        .map_err(|e| format!("pip 执行失败: {}", e))?;

    if output.status.success() {
        Ok("依赖安装成功".into())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr);
        Err(format!("pip install 失败:\n{}", stderr))
    }
}

// ── Superadmin Commands ──────────────────────────────────────────

fn reg_keys_path(project_dir: &std::path::Path) -> PathBuf {
    project_dir.join("config").join("registration_keys.json")
}

fn users_json_path(project_dir: &std::path::Path) -> PathBuf {
    project_dir.join("users").join("users.json")
}

fn load_reg_keys_file(project_dir: &std::path::Path) -> Result<RegKeysFile, String> {
    let path = reg_keys_path(project_dir);
    if !path.exists() {
        return Ok(RegKeysFile {
            description: Some("OpenJellyfish 注册码".into()),
            keys: Vec::new(),
        });
    }
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&content).map_err(|e| format!("解析注册码文件失败: {}", e))
}

fn save_reg_keys_file(project_dir: &std::path::Path, file: &RegKeysFile) -> Result<(), String> {
    let path = reg_keys_path(project_dir);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let json = serde_json::to_string_pretty(file).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())
}

fn generate_key_string() -> String {
    let mut rng = rand::thread_rng();
    let seg = |r: &mut rand::rngs::ThreadRng| -> String {
        let chars: Vec<char> = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789".chars().collect();
        (0..4).map(|_| chars[r.gen_range(0..chars.len())]).collect()
    };
    format!("OJF-{}-{}-{}", seg(&mut rng), seg(&mut rng), seg(&mut rng))
}

#[tauri::command]
fn list_registration_keys(state: State<'_, AppState>) -> Result<Vec<RegKeyInfo>, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let file = load_reg_keys_file(&project_dir)?;
    Ok(file.keys)
}

#[tauri::command]
fn generate_registration_keys(
    count: u8,
    state: State<'_, AppState>,
) -> Result<Vec<String>, String> {
    let count = count.clamp(1, 20);
    let project_dir = state.project_dir.lock().unwrap().clone();
    let mut file = load_reg_keys_file(&project_dir)?;
    let now = Utc::now().to_rfc3339();

    let mut new_keys = Vec::new();
    for _ in 0..count {
        let key = generate_key_string();
        new_keys.push(key.clone());
        file.keys.push(RegKeyInfo {
            key,
            used: false,
            created_at: Some(now.clone()),
            used_by: None,
            used_at: None,
        });
    }

    save_reg_keys_file(&project_dir, &file)?;
    Ok(new_keys)
}

#[tauri::command]
fn delete_registration_key(key: String, state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let mut file = load_reg_keys_file(&project_dir)?;

    let idx = file
        .keys
        .iter()
        .position(|k| k.key == key)
        .ok_or("注册码不存在")?;

    if file.keys[idx].used {
        return Err("已使用的注册码不可删除".into());
    }

    file.keys.remove(idx);
    save_reg_keys_file(&project_dir, &file)
}

#[tauri::command]
fn list_admin_users(state: State<'_, AppState>) -> Result<Vec<AdminUserInfo>, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = users_json_path(&project_dir);

    if !path.exists() {
        return Ok(Vec::new());
    }

    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let users: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| format!("解析用户文件失败: {}", e))?;

    let obj = users.as_object().ok_or("users.json 格式错误")?;
    let mut result = Vec::new();

    for (uid, udata) in obj {
        let has_api_keys = project_dir
            .join("users")
            .join(uid)
            .join("api_keys.json")
            .exists();

        result.push(AdminUserInfo {
            user_id: uid.clone(),
            username: udata
                .get("username")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            created_at: udata
                .get("created_at")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            last_login: udata
                .get("last_login")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            reg_key: udata
                .get("reg_key")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            has_api_keys,
            disabled: udata.get("disabled").and_then(|v| v.as_bool()).unwrap_or(false),
        });
    }

    result.sort_by(|a, b| b.created_at.cmp(&a.created_at));
    Ok(result)
}

#[tauri::command]
fn reset_admin_password(user_id: String, state: State<'_, AppState>) -> Result<ResetPasswordResult, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = users_json_path(&project_dir);
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut users: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let obj = users.as_object_mut().ok_or("users.json 格式错误")?;
    let user = obj
        .get_mut(&user_id)
        .ok_or("用户不存在")?
        .as_object_mut()
        .ok_or("用户数据格式错误")?;

    let mut rng = rand::thread_rng();
    let temp_password: String = (0..12)
        .map(|_| {
            let chars: Vec<char> = "abcdefghjkmnpqrstuvwxyz23456789".chars().collect();
            chars[rng.gen_range(0..chars.len())]
        })
        .collect();

    // sha256:{salt}:{hash} format compatible with Python backend
    let salt: String = (0..16)
        .map(|_| {
            let chars: Vec<char> = "abcdef0123456789".chars().collect();
            chars[rng.gen_range(0..chars.len())]
        })
        .collect();
    let mut hasher = Sha256::new();
    hasher.update(format!("{}{}", salt, temp_password));
    let hash_hex = hex::encode(hasher.finalize());
    let password_hash = format!("sha256:{}:{}", salt, hash_hex);

    user.insert("password_hash".into(), serde_json::Value::String(password_hash));

    let json = serde_json::to_string_pretty(&users).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())?;

    Ok(ResetPasswordResult {
        user_id,
        temp_password,
    })
}

#[tauri::command]
fn delete_admin_user(user_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = users_json_path(&project_dir);
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut users: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let obj = users.as_object_mut().ok_or("users.json 格式错误")?;
    if obj.remove(&user_id).is_none() {
        return Err("用户不存在".into());
    }

    let json = serde_json::to_string_pretty(&users).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())?;

    // Also remove user directory
    let user_dir = project_dir.join("users").join(&user_id);
    if user_dir.exists() {
        let _ = fs::remove_dir_all(&user_dir);
    }

    Ok(())
}

#[tauri::command]
fn set_admin_disabled(
    user_id: String,
    disabled: bool,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = users_json_path(&project_dir);
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut users: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let obj = users.as_object_mut().ok_or("users.json 格式错误")?;
    let user = obj
        .get_mut(&user_id)
        .ok_or("用户不存在")?
        .as_object_mut()
        .ok_or("用户数据格式错误")?;

    user.insert("disabled".into(), serde_json::Value::Bool(disabled));
    // 封禁时清掉 token，强制现有会话失效
    if disabled {
        user.insert("token".into(), serde_json::Value::String(String::new()));
    }

    let json = serde_json::to_string_pretty(&users).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())?;
    Ok(())
}

#[tauri::command]
fn rename_admin_user(
    user_id: String,
    new_username: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let new_username = new_username.trim().to_string();
    if new_username.is_empty() {
        return Err("用户名不能为空".into());
    }
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = users_json_path(&project_dir);
    let content = fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let mut users: serde_json::Value =
        serde_json::from_str(&content).map_err(|e| e.to_string())?;

    let obj = users.as_object_mut().ok_or("users.json 格式错误")?;

    // 重名检查（排除自己）
    for (uid, udata) in obj.iter() {
        if uid == &user_id {
            continue;
        }
        if udata.get("username").and_then(|v| v.as_str()) == Some(new_username.as_str()) {
            return Err(format!("用户名 \"{}\" 已被占用", new_username));
        }
    }

    let user = obj
        .get_mut(&user_id)
        .ok_or("用户不存在")?
        .as_object_mut()
        .ok_or("用户数据格式错误")?;
    user.insert("username".into(), serde_json::Value::String(new_username));

    let json = serde_json::to_string_pretty(&users).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| e.to_string())?;
    Ok(())
}

fn dir_size_bytes(path: &std::path::Path) -> u64 {
    use walkdir::WalkDir;
    let mut total: u64 = 0;
    for entry in WalkDir::new(path).into_iter().filter_map(|e| e.ok()) {
        if let Ok(meta) = entry.metadata() {
            if meta.is_file() {
                total += meta.len();
            }
        }
    }
    total
}

#[tauri::command]
fn get_admin_storage(
    user_id: String,
    state: State<'_, AppState>,
) -> Result<AdminStorage, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let user_dir = project_dir.join("users").join(&user_id);
    if !user_dir.exists() {
        return Ok(AdminStorage {
            user_id,
            total_bytes: 0,
            breakdown: Vec::new(),
        });
    }

    let mut breakdown: Vec<StorageBreakdown> = Vec::new();
    let mut total: u64 = 0;
    for entry in fs::read_dir(&user_dir).map_err(|e| e.to_string())?.flatten() {
        let p = entry.path();
        let name = entry.file_name().to_string_lossy().to_string();
        let bytes = if p.is_dir() {
            dir_size_bytes(&p)
        } else {
            entry.metadata().map(|m| m.len()).unwrap_or(0)
        };
        total += bytes;
        breakdown.push(StorageBreakdown { name, bytes });
    }
    breakdown.sort_by(|a, b| b.bytes.cmp(&a.bytes));

    Ok(AdminStorage {
        user_id,
        total_bytes: total,
        breakdown,
    })
}

#[tauri::command]
fn get_admin_key_status(
    user_id: String,
    state: State<'_, AppState>,
) -> Result<AdminKeyStatus, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();
    let path = project_dir
        .join("users")
        .join(&user_id)
        .join("api_keys.json");

    let mut providers: Vec<String> = Vec::new();
    if path.exists() {
        if let Ok(content) = fs::read_to_string(&path) {
            if let Ok(val) = serde_json::from_str::<serde_json::Value>(&content) {
                if let Some(obj) = val.as_object() {
                    // 字段名 → 友好供应商名（仅密钥字段，密文非空即视为已配置）
                    let map: &[(&str, &str)] = &[
                        ("openai_api_key", "OpenAI"),
                        ("anthropic_api_key", "Anthropic"),
                        ("tavily_api_key", "Tavily"),
                        ("cloudsway_search_key", "CloudsWay"),
                        ("image_api_key", "图像"),
                        ("tts_api_key", "TTS"),
                        ("video_api_key", "视频"),
                        ("s2s_api_key", "实时语音"),
                        ("stt_api_key", "STT"),
                        ("kimi_api_key", "Kimi"),
                        ("minimax_api_key", "MiniMax"),
                        ("doubao_access_key", "豆包"),
                        ("bedrock_api_key", "Bedrock"),
                    ];
                    for (field, label) in map {
                        let configured = obj
                            .get(*field)
                            .and_then(|v| v.as_str())
                            .map(|s| !s.is_empty())
                            .unwrap_or(false);
                        if configured {
                            providers.push(label.to_string());
                        }
                    }
                }
            }
        }
    }

    Ok(AdminKeyStatus { user_id, providers })
}

#[tauri::command]
fn get_admin_stats(state: State<'_, AppState>) -> Result<AdminStats, String> {
    let project_dir = state.project_dir.lock().unwrap().clone();

    // Registration keys stats
    let reg_file = load_reg_keys_file(&project_dir)?;
    let total_reg_keys = reg_file.keys.len();
    let used_reg_keys = reg_file.keys.iter().filter(|k| k.used).count();

    // User stats
    let users_path = users_json_path(&project_dir);
    if !users_path.exists() {
        return Ok(AdminStats {
            total_users: 0,
            active_7d: 0,
            keys_configured: 0,
            total_reg_keys,
            used_reg_keys,
            available_reg_keys: total_reg_keys - used_reg_keys,
        });
    }

    let content = fs::read_to_string(&users_path).map_err(|e| e.to_string())?;
    let users: serde_json::Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;
    let obj = users.as_object().ok_or("users.json 格式错误")?;

    let total_users = obj.len();
    let seven_days_ago = Utc::now() - chrono::Duration::days(7);

    let mut active_7d = 0;
    let mut keys_configured = 0;

    for (uid, udata) in obj {
        if let Some(last_login) = udata.get("last_login").and_then(|v| v.as_str()) {
            if let Ok(dt) = chrono::DateTime::parse_from_rfc3339(last_login) {
                if dt > seven_days_ago {
                    active_7d += 1;
                }
            } else if let Ok(dt) = chrono::NaiveDateTime::parse_from_str(last_login, "%Y-%m-%dT%H:%M:%S%.f") {
                if dt > seven_days_ago.naive_utc() {
                    active_7d += 1;
                }
            }
        }

        if project_dir
            .join("users")
            .join(uid)
            .join("api_keys.json")
            .exists()
        {
            keys_configured += 1;
        }
    }

    Ok(AdminStats {
        total_users,
        active_7d,
        keys_configured,
        total_reg_keys,
        used_reg_keys,
        available_reg_keys: total_reg_keys - used_reg_keys,
    })
}

// ── Token Usage Stats (P3) ───────────────────────────────────────
// 直读 users/{admin}/llm_usage/usage-YYYY-MM.jsonl（P0 落盘），按用户/模型/
// 渠道/日期聚合。纯文件读，不走后端 HTTP——符合「启动器读文件」原则。

#[derive(Serialize, Default, Clone)]
struct UsageBucket {
    calls: u64,
    input_tokens: u64,
    output_tokens: u64,
    total_tokens: u64,
}

impl UsageBucket {
    fn add(&mut self, inp: u64, out: u64, tot: u64) {
        self.calls += 1;
        self.input_tokens += inp;
        self.output_tokens += out;
        self.total_tokens += tot;
    }
}

#[derive(Serialize)]
struct NamedBucket {
    name: String,
    calls: u64,
    input_tokens: u64,
    output_tokens: u64,
    total_tokens: u64,
}

#[derive(Serialize)]
struct TokenUsageStats {
    total: UsageBucket,
    by_user: Vec<NamedBucket>,
    by_model: Vec<NamedBucket>,
    by_channel: Vec<NamedBucket>,
    by_day: Vec<NamedBucket>,
    months_scanned: u32,
}

#[derive(Deserialize)]
struct UsageRecord {
    #[serde(default)]
    model: String,
    #[serde(default)]
    input_tokens: i64,
    #[serde(default)]
    output_tokens: i64,
    #[serde(default)]
    total_tokens: i64,
    #[serde(default)]
    ts: String,
    #[serde(default)]
    channel: String,
}

fn bucket_map_to_sorted_vec(map: HashMap<String, UsageBucket>, by_name: bool) -> Vec<NamedBucket> {
    let mut v: Vec<NamedBucket> = map
        .into_iter()
        .map(|(name, b)| NamedBucket {
            name,
            calls: b.calls,
            input_tokens: b.input_tokens,
            output_tokens: b.output_tokens,
            total_tokens: b.total_tokens,
        })
        .collect();
    if by_name {
        // 日期升序（YYYY-MM-DD 字典序即时间序）
        v.sort_by(|a, b| a.name.cmp(&b.name));
    } else {
        // 用量降序（token 多的在前）
        v.sort_by(|a, b| b.total_tokens.cmp(&a.total_tokens));
    }
    v
}

#[tauri::command]
fn get_token_usage_stats(months: Option<u32>, state: State<'_, AppState>) -> TokenUsageStats {
    let months = months.unwrap_or(6).clamp(1, 24);
    let project_dir = state.project_dir.lock().unwrap().clone();
    let users_dir = project_dir.join("users");

    // uid → username（用于面板友好显示）
    let mut uid_to_name: HashMap<String, String> = HashMap::new();
    if let Ok(content) = fs::read_to_string(users_json_path(&project_dir)) {
        if let Ok(users) = serde_json::from_str::<serde_json::Value>(&content) {
            if let Some(obj) = users.as_object() {
                for (uid, udata) in obj {
                    if let Some(name) = udata.get("username").and_then(|v| v.as_str()) {
                        uid_to_name.insert(uid.clone(), name.to_string());
                    }
                }
            }
        }
    }

    let mut total = UsageBucket::default();
    let mut by_user: HashMap<String, UsageBucket> = HashMap::new();
    let mut by_model: HashMap<String, UsageBucket> = HashMap::new();
    let mut by_channel: HashMap<String, UsageBucket> = HashMap::new();
    let mut by_day: HashMap<String, UsageBucket> = HashMap::new();

    if let Ok(entries) = fs::read_dir(&users_dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if !path.is_dir() {
                continue;
            }
            let admin_id = entry.file_name().to_string_lossy().to_string();
            let llm_dir = path.join("llm_usage");
            if !llm_dir.is_dir() {
                continue;
            }
            // 收集 usage-*.jsonl，按文件名倒序（最近月份在前），取最近 months 个
            let mut files: Vec<PathBuf> = Vec::new();
            if let Ok(rd) = fs::read_dir(&llm_dir) {
                for f in rd.flatten() {
                    let fname = f.file_name().to_string_lossy().to_string();
                    if fname.starts_with("usage-") && fname.ends_with(".jsonl") {
                        files.push(f.path());
                    }
                }
            }
            files.sort_by(|a, b| b.file_name().cmp(&a.file_name()));
            files.truncate(months as usize);

            let display_name = uid_to_name
                .get(&admin_id)
                .cloned()
                .unwrap_or_else(|| admin_id.clone());

            for file in files {
                let content = match fs::read_to_string(&file) {
                    Ok(c) => c,
                    Err(_) => continue,
                };
                for line in content.lines() {
                    let line = line.trim();
                    if line.is_empty() {
                        continue;
                    }
                    let rec: UsageRecord = match serde_json::from_str(line) {
                        Ok(r) => r,
                        Err(_) => continue,
                    };
                    let inp = rec.input_tokens.max(0) as u64;
                    let out = rec.output_tokens.max(0) as u64;
                    let tot = if rec.total_tokens > 0 {
                        rec.total_tokens as u64
                    } else {
                        inp + out
                    };
                    total.add(inp, out, tot);
                    by_user.entry(display_name.clone()).or_default().add(inp, out, tot);
                    let model = if rec.model.is_empty() {
                        "(未知)".to_string()
                    } else {
                        rec.model.clone()
                    };
                    by_model.entry(model).or_default().add(inp, out, tot);
                    let channel = if rec.channel.is_empty() {
                        "(其它)".to_string()
                    } else {
                        rec.channel.clone()
                    };
                    by_channel.entry(channel).or_default().add(inp, out, tot);
                    let day: String = rec.ts.chars().take(10).collect();
                    if day.len() == 10 {
                        by_day.entry(day).or_default().add(inp, out, tot);
                    }
                }
            }
        }
    }

    TokenUsageStats {
        total,
        by_user: bucket_map_to_sorted_vec(by_user, false),
        by_model: bucket_map_to_sorted_vec(by_model, false),
        by_channel: bucket_map_to_sorted_vec(by_channel, false),
        by_day: bucket_map_to_sorted_vec(by_day, true),
        months_scanned: months,
    }
}

// ── App ──────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(AppState {
            jellyfish_process: Mutex::new(None),
            project_dir: Mutex::new(find_project_dir_dev()),
            backend_port: Mutex::new(8000),
            frontend_port: Mutex::new(3000),
        })
        .on_window_event(|window, event| {
            // 用户点 X 关闭窗口时，先把后台 launcher.py 及其孙子进程
            // (uvicorn / express) 全部杀掉，避免端口残留 / 服务未停。
            // 不 prevent close —— 杀完直接让窗口正常关闭。
            if let WindowEvent::CloseRequested { .. } = event {
                if let Some(state) = window.try_state::<AppState>() {
                    let _ = shutdown_jellyfish(&state);
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            detect_environment,
            load_env_config,
            save_env_config,
            test_api_key,
            start_jellyfish,
            stop_jellyfish,
            get_status,
            get_lan_ip,
            open_in_browser,
            install_pip_deps,
            open_project_dir,
            open_users_dir,
            open_logs_dir,
            open_release_page,
            get_app_version,
            pack_backup,
            list_registration_keys,
            generate_registration_keys,
            delete_registration_key,
            list_admin_users,
            reset_admin_password,
            delete_admin_user,
            set_admin_disabled,
            rename_admin_user,
            get_admin_storage,
            get_admin_key_status,
            get_admin_stats,
            get_token_usage_stats,
            get_system_health,
            get_disk_usage,
            tail_log,
        ])
        .build(tauri::generate_context!())
        .expect("failed to build OpenJellyfish launcher")
        .run(|app, event| {
            // 双保险：即便 CloseRequested 没触发（比如系统强制 Exit），
            // 在 RunEvent::Exit 阶段再清一次进程树。
            if let RunEvent::Exit = event {
                if let Some(state) = app.try_state::<AppState>() {
                    let _ = shutdown_jellyfish(&state);
                }
            }
        });
}
