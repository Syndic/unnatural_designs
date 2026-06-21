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

// Styler renders terminal output in the appropriate visual treatment for
// the destination — colored ANSI when the underlying stream supports it,
// pipe-safe fallbacks otherwise. Each method picks both the color codes
// and any structural choice (bracketed vs padded labels, ASCII vs Unicode
// progress runes, present vs omitted box) that depends on color support,
// so callers never have to branch on display state themselves.
type Styler struct {
	enabled bool
}

// NewStyler creates a Styler for the given color mode and output file.
func NewStyler(mode string, file *os.File) (Styler, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "", ColorAuto:
		return Styler{enabled: shouldColor(file)}, nil
	case ColorAlways:
		return Styler{enabled: true}, nil
	case ColorNever:
		return Styler{enabled: false}, nil
	default:
		return Styler{}, fmt.Errorf("expected %s, %s, or %s", ColorAuto, ColorAlways, ColorNever)
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

// ANSI escape codes used by Styler. Pass/Warn/Fail use ANSI 2/3/1 so they
// track the user's terminal theme. Accent and Track are 256-color values
// reserved for the rich progress UI (spinner frames, progress-bar fill and
// track, banner accent).
const (
	codeReset  = "\033[0m"
	codePass   = "\033[32m"
	codeWarn   = "\033[33m"
	codeFail   = "\033[31m"
	codeAccent = "\033[38;5;215m"
	codeTrack  = "\033[38;5;240m"
	// blockPrefix* pack the three SGR parameters that make up a status block
	// — saturated ANSI bg, ANSI 0 fg, bold — into a single escape sequence
	// per role. Equivalent to emitting `\033[42m\033[30m\033[1m`, just terser.
	blockPrefixPass = "\033[42;30;1m" //nolint:gosec // ANSI SGR sequence, not credentials
	blockPrefixWarn = "\033[43;30;1m" //nolint:gosec // ANSI SGR sequence, not credentials
	blockPrefixFail = "\033[41;30;1m" //nolint:gosec // ANSI SGR sequence, not credentials
)

func (c Styler) wrap(code, text string) string {
	if !c.enabled {
		return text
	}
	return code + text + codeReset
}

// block renders text as a status block — saturated colored background with
// bold ANSI-0 (black) foreground, padded with a leading and trailing space
// so the colored region extends visibly wider than the glyph or label
// inside it. Under NO_COLOR the bare text is returned unchanged.
func (c Styler) block(prefix, text string) string {
	if !c.enabled {
		return text
	}
	return prefix + " " + text + " " + codeReset
}

func (c Styler) Pass(text string) string { return c.wrap(codePass, text) }
func (c Styler) Warn(text string) string { return c.wrap(codeWarn, text) }
func (c Styler) Fail(text string) string { return c.wrap(codeFail, text) }

// Accent wraps text in the warm-orange accent color. Used by the spinner,
// progress-bar fill/tip, and the banner accent character.
func (c Styler) Accent(text string) string { return c.wrap(codeAccent, text) }

// Track wraps text in the dim grey used for the unfilled portion of progress
// bars.
func (c Styler) Track(text string) string { return c.wrap(codeTrack, text) }

// BarRunes returns the rune set for an mpb-style progress bar. When colors
// are enabled it yields the accent-colored Unicode block fill / dim track;
// without color it falls back to the pipe-legible ASCII `[===>]` shape. The
// rune set and the color treatment are inseparable (Unicode block without
// color reads as a plain wall of solid characters), so they're chosen here
// together rather than by the caller branching on color state.
func (c Styler) BarRunes() (lbound, filler, tip, padding, rbound string) {
	if !c.enabled {
		return "[", "=", ">", " ", "]"
	}
	return "", c.Accent("█"), c.Accent("█"), c.Track("░"), ""
}

// Box wraps lines in a hairline box of the given inner width (the visible
// cell count between the │ borders). Each line must already be padded to
// width visible cells; Box doesn't strip ANSI to measure. Returns "" when
// the styler is disabled, so callers don't need to branch on state.
func (c Styler) Box(width int, lines []string) string {
	if !c.enabled {
		return ""
	}
	hr := strings.Repeat("─", width)
	var b strings.Builder
	b.WriteString("┌" + hr + "┐\n")
	for _, line := range lines {
		b.WriteString("│" + line + "│\n")
	}
	b.WriteString("└" + hr + "┘\n")
	return b.String()
}

// PassBlock / WarnBlock / FailBlock render their argument as a status block —
// saturated role-colored background with a bold black glyph inside, padded
// for visible width. When the styler is disabled they return the bare
// text, so the caller's bracket/glyph form is what survives on a pipe.
func (c Styler) PassBlock(text string) string { return c.block(blockPrefixPass, text) }
func (c Styler) WarnBlock(text string) string { return c.block(blockPrefixWarn, text) }
func (c Styler) FailBlock(text string) string { return c.block(blockPrefixFail, text) }

// Tag returns the report tag for status. When colors are enabled it renders
// as a padded saturated-bg block with bold black text (e.g. ` PASS ` on
// green). Under NO_COLOR it falls back to the bracket form (`[PASS]`) — the
// durable form that ships in piped output and CI logs.
func (c Styler) Tag(status string) string {
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
