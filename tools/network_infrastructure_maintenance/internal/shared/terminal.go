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

// ANSI escape codes used by Colorizer. Pass/Warn/Fail use ANSI 2/3/1 so they
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
	blockPrefixPass = "\033[42;30;1m"
	blockPrefixWarn = "\033[43;30;1m"
	blockPrefixFail = "\033[41;30;1m"
)

func (c Colorizer) wrap(code, text string) string {
	if !c.enabled {
		return text
	}
	return code + text + codeReset
}

// block renders text as a status block — saturated colored background with
// bold ANSI-0 (black) foreground, padded with a leading and trailing space
// so the colored region extends visibly wider than the glyph or label
// inside it. Under NO_COLOR the bare text is returned unchanged.
func (c Colorizer) block(prefix, text string) string {
	if !c.enabled {
		return text
	}
	return prefix + " " + text + " " + codeReset
}

func (c Colorizer) Pass(text string) string { return c.wrap(codePass, text) }
func (c Colorizer) Warn(text string) string { return c.wrap(codeWarn, text) }
func (c Colorizer) Fail(text string) string { return c.wrap(codeFail, text) }

// Accent wraps text in the warm-orange accent color. Used by the spinner,
// progress-bar fill/tip, and the banner accent character.
func (c Colorizer) Accent(text string) string { return c.wrap(codeAccent, text) }

// Track wraps text in the dim grey used for the unfilled portion of progress
// bars.
func (c Colorizer) Track(text string) string { return c.wrap(codeTrack, text) }

// BarRunes returns the rune set for an mpb-style progress bar. When colors
// are enabled it yields the accent-colored Unicode block fill / dim track;
// without color it falls back to the pipe-legible ASCII `[===>]` shape. The
// rune set and the color treatment are inseparable (Unicode block without
// color reads as a plain wall of solid characters), so they're chosen here
// together rather than by the caller branching on color state.
func (c Colorizer) BarRunes() (lbound, filler, tip, padding, rbound string) {
	if !c.enabled {
		return "[", "=", ">", " ", "]"
	}
	return "", c.Accent("█"), c.Accent("█"), c.Track("░"), ""
}

// PassBlock / WarnBlock / FailBlock render their argument as a status block —
// saturated role-colored background with a bold black glyph inside, padded
// for visible width. When the colorizer is disabled they return the bare
// text, so the caller's bracket/glyph form is what survives on a pipe.
func (c Colorizer) PassBlock(text string) string { return c.block(blockPrefixPass, text) }
func (c Colorizer) WarnBlock(text string) string { return c.block(blockPrefixWarn, text) }
func (c Colorizer) FailBlock(text string) string { return c.block(blockPrefixFail, text) }

// Tag returns the report tag for status. When colors are enabled it renders
// as a padded saturated-bg block with bold black text (e.g. ` PASS ` on
// green). Under NO_COLOR it falls back to the bracket form (`[PASS]`) — the
// durable form that ships in piped output and CI logs.
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
