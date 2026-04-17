package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"
	"time"

	netbox "github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/netbox"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/shared"
	"github.com/Syndic/unnatural_designs/tools/network_infrastructure_maintenance/internal/ui/progress"
)

var stderrColors shared.Colorizer

func fatalf(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "%s %s\n", stderrColors.Fail(shared.StatusFail), fmt.Sprintf(format, args...))
	os.Exit(1)
}

func main() {
	configEnvValue := strings.TrimSpace(os.Getenv(envNetBoxAuditCfg))
	var (
		baseURL        = flag.String(flagBaseURL, envOrDefault(shared.EnvNetBoxBaseURL, defaultBaseURL), "NetBox base URL")
		tokenFile      = flag.String(flagTokenFile, envOrDefault(shared.EnvNetBoxTokenFile, defaultTokenFile), "Path to NetBox API token file")
		configFile     = flag.String(flagConfig, envOrDefault(envNetBoxAuditCfg, defaultConfigPath()), "Path to audit policy JSON")
		format         = flag.String(flagFormat, shared.FormatText, "Output format: "+shared.FormatText+" or "+shared.FormatJSON)
		colorMode      = flag.String(flagColor, envOrDefault(envNetBoxAuditColor, shared.ColorAuto), "Color mode for text output: "+shared.ColorAuto+", "+shared.ColorAlways+", "+shared.ColorNever)
		progressMode   = flag.String(flagProgress, progress.ModeAutoName, "Progress UI mode: "+progress.ModeAutoName+", "+progress.ModeRichName+", "+progress.ModePlainName+", "+progress.ModeOffName)
		maxAttempts    = flag.Int(flagMaxAttempts, defaultMaxAttempt, "Maximum attempts to load a coherent snapshot")
		retryDelay     = flag.Duration(flagRetryDelay, 3*time.Second, "Delay between snapshot retries")
		failOnFindings = flag.Bool(flagFailOnFindings, false, "Exit non-zero if any check reports findings")
	)
	flag.Parse()

	if *format != shared.FormatText && *format != shared.FormatJSON {
		fatalf("Unsupported format %q (expected %s or %s)", *format, shared.FormatText, shared.FormatJSON)
	}
	if *maxAttempts < 1 {
		fatalf("max-attempts must be at least 1")
	}

	configExplicit := configEnvValue != ""
	flag.Visit(func(f *flag.Flag) {
		if f.Name == flagConfig {
			configExplicit = true
		}
	})

	stdoutColors, err := shared.NewColorizer(*colorMode, os.Stdout)
	if err != nil {
		fatalf("Invalid color mode %q: %v", *colorMode, err)
	}
	stderrColors = stdoutColors
	if colors, err := shared.NewColorizer(*colorMode, os.Stderr); err == nil {
		stderrColors = colors
	}

	token := strings.TrimSpace(os.Getenv(shared.EnvNetBoxToken))
	if token == "" {
		data, err := os.ReadFile(*tokenFile)
		if err != nil {
			fatalf("Failed to read token file %s: %v", *tokenFile, err)
		}
		token = strings.TrimSpace(string(data))
		if token == "" {
			fatalf("Token file %s is empty", *tokenFile)
		}
	}

	config, err := loadAuditConfig(*configFile, configExplicit)
	if err != nil {
		fatalf("Failed to load config %s: %v", *configFile, err)
	}

	registry := allChecks()
	checks, err := selectChecks(registry, config)
	if err != nil {
		fatalf("Invalid check selection: %v", err)
	}

	client := &netbox.Client{
		BaseURL: strings.TrimRight(*baseURL, "/"),
		Token:   token,
		HTTPClient: &http.Client{
			Timeout: 60 * time.Second,
			Transport: &http.Transport{
				MaxIdleConnsPerHost: netbox.SnapshotTaskCount(),
			},
		},
	}

	mode, err := progress.ParseMode(*progressMode)
	if err != nil {
		fatalf("Invalid progress mode %q: %v", *progressMode, err)
	}
	reporter := progress.New(os.Stderr, mode, stderrColors)
	defer func() { _ = reporter.Close() }()

	configLabel := "built-in defaults"
	if strings.TrimSpace(*configFile) != "" {
		configLabel = *configFile
	}
	reporter.Startupf("Starting NetBox audit against %s using %s", client.BaseURL, configLabel)
	checkIDs := make([]string, 0, len(checks))
	for _, c := range checks {
		checkIDs = append(checkIDs, c.ID())
	}
	reporter.AnnounceChecks(checkIDs)

	runStarted := time.Now()
	ctx := context.Background()
	snap, err := netbox.LoadConsistentSnapshot(ctx, client, *maxAttempts, *retryDelay, reporter)
	if err != nil {
		fatalf("Failed to load coherent NetBox snapshot: %v", err)
	}

	reporter.ChecksStart(len(checks))
	rep := runAudit(ctx, snap, config, checks, reporter)
	rep.Timing.Total = time.Since(runStarted)
	if err := reporter.Close(); err != nil {
		fatalf("Failed to flush progress renderer: %v", err)
	}

	switch *format {
	case shared.FormatJSON:
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		if err := enc.Encode(rep); err != nil {
			fatalf("Failed to write JSON report: %v", err)
		}
	default:
		printTextReport(rep, stdoutColors)
	}

	if *failOnFindings && totalFindings(rep) > 0 {
		os.Exit(2)
	}
}
