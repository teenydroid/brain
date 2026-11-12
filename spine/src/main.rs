//! `teeny-spine`: OpenHuman's command-line host, built from the library.
//!
//! It is what upstream's `openhuman-core` binary is (`crates/openhuman-cli`):
//! the shared host entry [`openhuman_rpc::host::cli`] does everything, so
//! `teeny-spine serve`, `teeny-spine cron list` and the rest behave exactly
//! as the upstream binary does. Teeny adds no behaviour here beyond linking
//! two modules in (TinyComputer and tinywallet) and answering the wallet
//! module's swap and bridge quotes from deBridge directly (`debridge.rs`);
//! what Teeny does lives in `brain/` and `bin/`. `bin/spine` runs it.

mod debridge;

use openhuman_rpc::embed::process;

fn main() {
    restore_default_sigpipe();

    // Before anything logs, as upstream's binary does. Not fatal: the
    // dispatcher loads it again and reports a failure itself.
    let _ = process::load_dotenv_for_cli();

    // TinyComputer and tinywallet, linked into this executable (spine/Cargo.toml).
    // Registering serves nothing by itself: the core loads a module from here
    // only where the config's `[modules] overrides` says `source = "linked"`.
    openhuman_modules_linked::install();

    // Swap and bridge quotes from deBridge's public API, ahead of the
    // tinyhumans proxy (which needs a signed-in session Teeny does not have).
    debridge::install();

    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(err) = openhuman_rpc::host::cli(&args) {
        eprintln!("{err}");
        std::process::exit(1);
    }
}

/// Rust ignores SIGPIPE at startup, which turns a write to a closed pipe
/// (`teeny-spine cron list | head`) into a panic. A command-line tool should
/// stop quietly instead.
#[cfg(unix)]
#[allow(unsafe_code)]
fn restore_default_sigpipe() {
    unsafe {
        libc::signal(libc::SIGPIPE, libc::SIG_DFL);
    }
}

#[cfg(not(unix))]
fn restore_default_sigpipe() {}
