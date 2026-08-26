// Package codeqlgateprobe contains deliberately insecure code and exists only to prove that a
// high-severity CodeQL finding blocks a merge.
//
// The repo's `code_scanning` ruleset rule names CodeQL at `security=high_or_higher` and
// `alerts=errors`, and has never had a qualifying alert to act on -- every CodeQL alert in this
// repo's history was one rule at medium/warning, below both thresholds. So "it has never blocked
// anything" and "it does not work" were indistinguishable from the outside. This package makes it
// fire once so they can be told apart.
//
// Branched from main rather than from the extraction-report branch on purpose: a CodeQL job that
// fails for its own reasons would block the merge too, and prove nothing about this rule.
//
// Not for merging. Delete the branch once the block has been observed.
package codeqlgateprobe

import (
	"crypto/md5" //nolint:gosec // deliberate; see the package comment
	"encoding/hex"
	"net/http"
	"os"
)

// HashSecret trips `go/weak-cryptographic-algorithm` (security-severity 7.5, high). A local query
// rather than a taint-tracked one, so it fires whether or not the stdlib extraction gap is open --
// which is what makes it the reliable half of this probe.
func HashSecret(secret string) string {
	sum := md5.Sum([]byte(secret)) //nolint:gosec // deliberate; see the package comment
	return hex.EncodeToString(sum[:])
}

// ServeFile trips `go/path-injection` (security-severity 7.5, problem.severity error), which is
// the only half that also exercises the rule's `alerts=errors` threshold. Taint-tracked, so a
// negative here is inconclusive rather than evidence: it could equally mean the query did not
// model this source.
func ServeFile(w http.ResponseWriter, r *http.Request) {
	body, err := os.ReadFile(r.URL.Query().Get("path")) //nolint:gosec // deliberate
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	_, _ = w.Write(body) //nolint:gosec // deliberate; see the package comment
}
