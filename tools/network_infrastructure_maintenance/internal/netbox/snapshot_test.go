package netbox

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// recordingObserver captures the per-task progress callbacks created by
// SnapshotTaskStart and records every (name, items, total, requests) tick.
type recordingObserver struct {
	mu        sync.Mutex
	starts    []string
	completes []string
	ticks     map[string]int // task name -> number of progress callbacks fired
	loadErrs  []error
	delays    []time.Duration
}

func newRecordingObserver() *recordingObserver {
	return &recordingObserver{ticks: make(map[string]int)}
}

func (o *recordingObserver) SnapshotAttemptStart(int, int, int) {}

func (o *recordingObserver) SnapshotTaskStart(name string) TaskProgress {
	o.mu.Lock()
	o.starts = append(o.starts, name)
	o.mu.Unlock()
	return func(items, total, reqs int) {
		o.mu.Lock()
		defer o.mu.Unlock()
		o.ticks[name]++
	}
}

func (o *recordingObserver) SnapshotTaskComplete(_ int, _ int, stats FetchTiming, _ int) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.completes = append(o.completes, stats.Name)
}

func (o *recordingObserver) SnapshotLoadError(_ int, _ int, err error) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.loadErrs = append(o.loadErrs, err)
}

func (o *recordingObserver) SnapshotLoadRetryDelay(d time.Duration) {
	o.mu.Lock()
	defer o.mu.Unlock()
	o.delays = append(o.delays, d)
}

// TestSnapshotTaskProgressPlumbing verifies that the per-task progress
// callback returned from SnapshotTaskStart is plumbed all the way through
// snapshotTask.run -> fetchAll -> FetchAllWithProgress and actually fires
// for every task during a snapshot load.
func TestSnapshotTaskProgressPlumbing(t *testing.T) {
	// Empty NetBox: every collection returns zero items in a single page.
	// One page still triggers exactly one progress callback per task.
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	defer srv.Close()

	obs := newRecordingObserver()
	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}

	// LatestChange is called twice (start/end) by LoadConsistentSnapshot;
	// since the same handler answers everything with count:0, both calls
	// return an empty ObjectChange so the snapshot is considered coherent.
	snap, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 1 {
		t.Errorf("SnapshotAttempts=%d, want 1", snap.SnapshotAttempts)
	}

	wantTasks := SnapshotTaskCount()
	if got := len(obs.starts); got != wantTasks {
		t.Errorf("SnapshotTaskStart fired %d times, want %d", got, wantTasks)
	}
	if got := len(obs.completes); got != wantTasks {
		t.Errorf("SnapshotTaskComplete fired %d times, want %d", got, wantTasks)
	}
	if got := len(obs.ticks); got != wantTasks {
		t.Errorf("progress callback fired for %d distinct tasks, want %d", got, wantTasks)
	}
	for name, count := range obs.ticks {
		if count != 1 {
			t.Errorf("task %q: progress fired %d times, want 1 (one page)", name, count)
		}
	}
}

// TestSnapshotTaskProgressNilObserver confirms loadSnapshot tolerates a nil
// observer (passes nil progress through cleanly).
func TestSnapshotTaskProgressNilObserver(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	defer srv.Close()

	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	if _, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, nil); err != nil {
		t.Fatalf("LoadConsistentSnapshot with nil observer: %v", err)
	}
}

// newChangeMovingServer answers LatestChange with change ID 1 on attempt 1's
// start read and 2 on every read after, so attempt 1 sees NetBox move and
// attempt 2 sees it stable. Every other endpoint returns an empty page.
func newChangeMovingServer(t *testing.T) *httptest.Server {
	t.Helper()
	var changeReads atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/core/object-changes/" {
			id := 2
			if changeReads.Add(1) == 1 {
				id = 1
			}
			_, _ = fmt.Fprintf(w, `{"count":1,"next":null,"results":[{"id":%d}]}`, id)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	t.Cleanup(srv.Close)
	return srv
}

// TestLoadConsistentSnapshotRetriesNilObserver covers the retry path with a nil
// observer, which must not be dereferenced before the retry delay.
func TestLoadConsistentSnapshotRetriesNilObserver(t *testing.T) {
	srv := newChangeMovingServer(t)
	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, 0, nil)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
}

// TestLoadConsistentSnapshotRetriesReportsToObserver checks that a change
// between an attempt's start and end reads is reported once as a load error
// and once as a retry delay.
func TestLoadConsistentSnapshotRetriesReportsToObserver(t *testing.T) {
	srv := newChangeMovingServer(t)
	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	obs := newRecordingObserver()

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, 0, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	} else if !strings.Contains(obs.loadErrs[0].Error(), "(1 -> 2)") {
		t.Errorf("load error %q does not name the moved change IDs", obs.loadErrs[0])
	}
	if got := len(obs.delays); got != 1 {
		t.Errorf("SnapshotLoadRetryDelay fired %d times, want 1", got)
	}
}

// TestLoadSnapshotJoinsTaskErrors verifies that when multiple fetch tasks fail,
// the returned error annotates each failure with its task name and exposes the
// underlying HTTP error through the unwrap chain (errors.Join + %w).
func TestLoadSnapshotJoinsTaskErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Fail two task endpoints with HTTP 500; respond empty to everything else
		// (including LatestChange's /api/core/object-changes/).
		if strings.Contains(r.URL.Path, "/api/dcim/devices/") || strings.Contains(r.URL.Path, "/api/dcim/cables/") {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	defer srv.Close()

	client := &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
	_, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, nil)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	msg := err.Error()
	for _, taskName := range []string{"devices:", "cables:"} {
		if !strings.Contains(msg, taskName) {
			t.Errorf("error %q does not mention failing task %q", msg, taskName)
		}
	}
	// Per-task wrap uses %w, so the underlying HTTP 500 message bubbles up.
	if !strings.Contains(msg, "HTTP 500") {
		t.Errorf("error %q does not include underlying HTTP 500", msg)
	}
	// errors.Join produces a value implementing Unwrap() []error. This is the
	// concrete behavioral difference vs. the old errors.New(strings.Join(...))
	// form, which produced a flat *errors.errorString with no walkable chain.
	type multiUnwrap interface{ Unwrap() []error }
	mu, ok := err.(multiUnwrap)
	if !ok {
		t.Fatalf("error %T does not implement Unwrap() []error; chain is not walkable", err)
	}
	if got := len(mu.Unwrap()); got != 2 {
		t.Errorf("Unwrap() returned %d errors, want 2", got)
	}
}
