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
	info, err := file.Stat()
	if err != nil {
		return false
	}
	return (info.Mode() & os.ModeCharDevice) != 0
}

func (c Colorizer) wrap(code, text string) string {
	if !c.enabled {
		return text
	}
	return code + text + "\033[0m"
}

func (c Colorizer) Pass(text string) string {
	return c.wrap("\033[32m", text)
}

func (c Colorizer) Warn(text string) string {
	return c.wrap("\033[38;5;214m", text)
}

func (c Colorizer) Fail(text string) string {
	return c.wrap("\033[31m", text)
}
