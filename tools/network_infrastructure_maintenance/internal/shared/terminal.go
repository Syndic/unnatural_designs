package shared

import (
	"fmt"
	"os"
	"strings"
	"time"
)

// FormatDuration formats a duration for human-readable terminal output.
func FormatDuration(d time.Duration) string {
	if d <= 0 {
		return "0s"
	}
	if d < time.Millisecond {
		return d.Round(time.Microsecond).String()
	}
	if d < time.Second {
		return d.Round(time.Millisecond).String()
	}
	return d.Round(10 * time.Millisecond).String()
}

// Colorizer wraps text in ANSI escape codes when color is enabled.
type Colorizer struct {
	enabled bool
}

// NewColorizer creates a Colorizer for the given color mode and output file.
func NewColorizer(mode string, file *os.File) (Colorizer, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "", ColorAuto:
		return Colorizer{enabled: shouldColor(file)}, nil
	case ColorAlways:
		return Colorizer{enabled: true}, nil
	case ColorNever:
		return Colorizer{enabled: false}, nil
	default:
		return Colorizer{}, fmt.Errorf("expected %s, %s, or %s", ColorAuto, ColorAlways, ColorNever)
	}
}

// shouldColor reports whether color output is appropriate for the given file.
func shouldColor(file *os.File) bool {
	if os.Getenv(EnvNoColor) != "" {
		return false
	}
	if term := os.Getenv("TERM"); term == "" || term == TermDumb {
		return false
	}
	return IsTerminal(file)
}

// IsTerminal reports whether file is connected to an interactive terminal.
func IsTerminal(file *os.File) bool {
	info, err := file.Stat()
	if err != nil {
		return false
	}
	return (info.Mode() & os.ModeCharDevice) != 0
}

// ANSI escape codes used by Colorizer. Pass/Warn/Fail track the user's
// terminal theme via ANSI 2/3/1 (spec §03). Accent is the brand wire-glow
// (the single sanctioned 256-color value — no ANSI orange exists) and is
// reserved for spinner frames, the progress-bar fill, the cursor block, and
// the banner underscore (spec §04).
const (
	codeReset  = "\033[0m"
	codeBold   = "\033[1m"
	codePass   = "\033[32m"
	codeWarn   = "\033[33m"
	codeFail   = "\033[31m"
	codeAccent = "\033[38;5;215m"
	codeTrack  = "\033[38;5;240m"
	codeBgPass = "\033[42m"
	codeBgWarn = "\033[43m"
	codeBgFail = "\033[41m"
	codeFgOnBlock = "\033[30m" // ANSI 0, paired with SGR 1 for bold weight
)

// Enabled reports whether the colorizer will emit ANSI codes. Callers use it
// to switch between brand-aligned color treatments and pipe-safe fallbacks
// without poking at the unexported state.
func (c Colorizer) Enabled() bool { return c.enabled }

func (c Colorizer) wrap(code, text string) string {
	if !c.enabled {
		return text
	}
	return code + text + codeReset
}

// block renders text as a saturated colored background with bold ANSI-0
// (black) foreground, padded with a leading and trailing space so the colored
// region extends visibly wider than the glyph or label inside it. Under
// NO_COLOR the bare text is returned unchanged.
func (c Colorizer) block(bg, text string) string {
	if !c.enabled {
		return text
	}
	return bg + codeFgOnBlock + codeBold + " " + text + " " + codeReset
}

func (c Colorizer) Pass(text string) string { return c.wrap(codePass, text) }
func (c Colorizer) Warn(text string) string { return c.wrap(codeWarn, text) }
func (c Colorizer) Fail(text string) string { return c.wrap(codeFail, text) }

// Accent wraps text in the brand wire-glow color. Reserved per spec §03 —
// spinner frames, progress-bar fill/tip, cursor, banner underscore.
func (c Colorizer) Accent(text string) string { return c.wrap(codeAccent, text) }

// Track wraps text in the brand line-2 dim grey, used for the unfilled
// portion of progress bars (spec §06).
func (c Colorizer) Track(text string) string { return c.wrap(codeTrack, text) }

// PassBlock / WarnBlock / FailBlock render their argument as a Direction A
// status block — saturated role-colored background with a bold black glyph
// inside, padded for visible width. When the colorizer is disabled they
// return the bare text, so the caller's bracket/glyph form is the pipe-safe
// fallback (spec §05).
func (c Colorizer) PassBlock(text string) string { return c.block(codeBgPass, text) }
func (c Colorizer) WarnBlock(text string) string { return c.block(codeBgWarn, text) }
func (c Colorizer) FailBlock(text string) string { return c.block(codeBgFail, text) }

// Tag returns the Direction A report tag for status. When colors are enabled
// it renders as a padded saturated-bg block with bold black text (e.g.
// ` PASS ` on green). Under NO_COLOR it falls back to the bracket form
// (`[PASS]`) — the durable-artifact form that ships in piped output and CI
// logs.
func (c Colorizer) Tag(status string) string {
	if !c.enabled {
		return "[" + status + "]"
	}
	switch status {
	case StatusPass:
		return c.PassBlock(status)
	case StatusWarn:
		return c.WarnBlock(status)
	case StatusFail:
		return c.FailBlock(status)
	default:
		return "[" + status + "]"
	}
}
