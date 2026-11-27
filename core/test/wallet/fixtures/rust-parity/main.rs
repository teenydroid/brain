// A throwaway driver around the unmodified spine/src/giveaway_tx.rs: the same
// entry the spine's main.rs calls, nothing else.
mod giveaway_tx;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::exit(giveaway_tx::run(&args));
}
