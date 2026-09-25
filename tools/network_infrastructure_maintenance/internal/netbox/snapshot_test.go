package netbox

import (
	"context"
	"errors"
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
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	obs := newRecordingObserver()

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

// TestSnapshotTaskProgressNilObserver confirms a nil observer is accepted on
// the single-attempt path.
func TestSnapshotTaskProgressNilObserver(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	if _, err := LoadConsistentSnapshot(context.Background(), client, 1, 0, nil); err != nil {
		t.Fatalf("LoadConsistentSnapshot with nil observer: %v", err)
	}
}

// newTestClient serves h for the life of the test and returns a Client for it.
func newTestClient(t *testing.T, h http.Handler) *Client {
	t.Helper()
	srv := httptest.NewServer(h)
	t.Cleanup(srv.Close)
	return &Client{BaseURL: srv.URL, Token: "x", HTTPClient: srv.Client()}
}

// newChangeClient answers the nth LatestChange read (from 1) with change ID
// changeID(n), and every other endpoint with an empty page.
func newChangeClient(t *testing.T, changeID func(n int32) int32) *Client {
	t.Helper()
	var changeReads atomic.Int32
	return newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/core/object-changes/" {
			_, _ = fmt.Fprintf(w, `{"count":1,"next":null,"results":[{"id":%d}]}`, changeID(changeReads.Add(1)))
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
}

// newChangeMovingClient reports change ID 1 on attempt 1's start read and 2 on
// every read after, so attempt 1 sees NetBox move and attempt 2 sees it stable.
func newChangeMovingClient(t *testing.T) *Client {
	return newChangeClient(t, func(n int32) int32 { return min(n, 2) })
}

// TestLoadConsistentSnapshotRetriesNilObserver covers the retry path with a nil
// observer, which must not be dereferenced before the retry delay.
func TestLoadConsistentSnapshotRetriesNilObserver(t *testing.T) {
	client := newChangeMovingClient(t)

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
// and once as a retry delay carrying the configured duration.
func TestLoadConsistentSnapshotRetriesReportsToObserver(t *testing.T) {
	client := newChangeMovingClient(t)
	obs := newRecordingObserver()

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, time.Millisecond, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	} else if !errors.Is(obs.loadErrs[0], errStateChanged) {
		t.Errorf("load error %q is not errStateChanged", obs.loadErrs[0])
	}
	if len(obs.delays) != 1 || obs.delays[0] != time.Millisecond {
		t.Errorf("SnapshotLoadRetryDelay calls = %v, want [1ms]", obs.delays)
	}
}

// TestLoadConsistentSnapshotGivesUpAfterMaxAttempts moves the change ID on
// every read, so no attempt is coherent: each attempt reports an error, only
// the gaps between attempts are delayed, and the last error is returned.
func TestLoadConsistentSnapshotGivesUpAfterMaxAttempts(t *testing.T) {
	client := newChangeClient(t, func(n int32) int32 { return n })
	obs := newRecordingObserver()

	_, err := LoadConsistentSnapshot(context.Background(), client, 3, time.Millisecond, obs)
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	if got := len(obs.loadErrs); got != 3 {
		t.Fatalf("SnapshotLoadError fired %d times, want 3: %v", got, obs.loadErrs)
	}
	if err != obs.loadErrs[2] {
		t.Errorf("returned error %q, want the last attempt's error %q", err, obs.loadErrs[2])
	}
	if got := len(obs.delays); got != 2 {
		t.Errorf("SnapshotLoadRetryDelay fired %d times, want 2 (none after the last attempt)", got)
	}
}

// TestLoadConsistentSnapshotRetriesAfterFetchError fails one fetch on attempt 1
// and checks that attempt 2 runs after the retry delay and succeeds.
func TestLoadConsistentSnapshotRetriesAfterFetchError(t *testing.T) {
	var deviceReads atomic.Int32
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/dcim/devices/" && deviceReads.Add(1) == 1 {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	obs := newRecordingObserver()

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, time.Millisecond, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	}
	if len(obs.delays) != 1 || obs.delays[0] != time.Millisecond {
		t.Errorf("SnapshotLoadRetryDelay calls = %v, want [1ms]", obs.delays)
	}
}

// cancellingObserver cancels the load's context when the retry delay begins.
type cancellingObserver struct {
	*recordingObserver
	cancel context.CancelFunc
}

func (o cancellingObserver) SnapshotLoadRetryDelay(time.Duration) { o.cancel() }

// TestLoadConsistentSnapshotRetryDelayHonoursCancel cancels the context at the
// start of an hour-long retry delay; the load must return context.Canceled
// rather than wait the delay out.
func TestLoadConsistentSnapshotRetryDelayHonoursCancel(t *testing.T) {
	client := newChangeMovingClient(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	obs := cancellingObserver{newRecordingObserver(), cancel}

	_, err := LoadConsistentSnapshot(ctx, client, 2, time.Hour, obs)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if !errors.Is(err, errStateChanged) {
		t.Errorf("err = %v, want it to keep the error that caused the retry", err)
	}
}

// TestLoadConsistentSnapshotRetriesAfterChangeReadError fails the first
// change-ID read and checks it is retried like any other failed request.
func TestLoadConsistentSnapshotRetriesAfterChangeReadError(t *testing.T) {
	var changeReads atomic.Int32
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/core/object-changes/" && changeReads.Add(1) == 1 {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	obs := newRecordingObserver()

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, time.Millisecond, obs)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	}
	if got := len(obs.delays); got != 1 {
		t.Errorf("SnapshotLoadRetryDelay fired %d times, want 1", got)
	}
}

// TestLoadConsistentSnapshotCancelledFetchIsNotRetried cancels the context from
// inside a fetch; the load must report the failed attempt and return
// context.Canceled without a retry.
func TestLoadConsistentSnapshotCancelledFetchIsNotRetried(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/dcim/devices/" {
			cancel()
			http.Error(w, "cancelled", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
	obs := newRecordingObserver()

	_, err := LoadConsistentSnapshot(ctx, client, 2, time.Hour, obs)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	}
	if len(obs.delays) != 0 {
		t.Errorf("retry delays %v reported, want none", obs.delays)
	}
}

// TestLoadSnapshotJoinsTaskErrors verifies that when multiple fetch tasks fail,
// the returned error annotates each failure with its task name and exposes the
// underlying HTTP error through the unwrap chain (errors.Join + %w).
func TestLoadSnapshotJoinsTaskErrors(t *testing.T) {
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Fail two task endpoints with HTTP 500; respond empty to everything else
		// (including LatestChange's /api/core/object-changes/).
		if strings.Contains(r.URL.Path, "/api/dcim/devices/") || strings.Contains(r.URL.Path, "/api/dcim/cables/") {
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))
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

func TestRetryable(t *testing.T) {
	permanent := &HTTPError{StatusCode: http.StatusUnauthorized}
	transient := &HTTPError{StatusCode: http.StatusBadGateway}
	for _, tc := range []struct {
		name string
		err  error
		want bool
	}{
		{"state changed", fmt.Errorf("%w (1 -> 2)", errStateChanged), true},
		{"transport failure", transientError{errors.New("connection refused")}, true},
		{"HTTP 500", &HTTPError{StatusCode: http.StatusInternalServerError}, true},
		{"HTTP 408", &HTTPError{StatusCode: http.StatusRequestTimeout}, true},
		{"HTTP 429", &HTTPError{StatusCode: http.StatusTooManyRequests}, true},
		{"HTTP 401", permanent, false},
		{"HTTP 403", &HTTPError{StatusCode: http.StatusForbidden}, false},
		{"HTTP 404", &HTTPError{StatusCode: http.StatusNotFound}, false},
		{"wrapped HTTP 502", fmt.Errorf("reading latest change: %w", transient), true},
		{"joined, all retryable", errors.Join(fmt.Errorf("a: %w", transient), transientError{errors.New("reset")}), true},
		{"joined, one permanent", errors.Join(fmt.Errorf("a: %w", transient), fmt.Errorf("b: %w", permanent)), false},
		{"unclassified error", errors.New("invalid character '<' looking for beginning of value"), false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := retryable(tc.err); got != tc.want {
				t.Errorf("retryable(%v) = %v, want %v", tc.err, got, tc.want)
			}
		})
	}
}

// TestLoadConsistentSnapshotPermanentFailureIsNotRetried checks that a failure
// no retry can fix ends the load on attempt 1, with no retry delay.
func TestLoadConsistentSnapshotPermanentFailureIsNotRetried(t *testing.T) {
	for _, tc := range []struct {
		name string
		fail map[string]int // request path -> status to answer it with
	}{
		{"401 on the first change read", map[string]int{"/api/core/object-changes/": http.StatusUnauthorized}},
		{"403 on a collection fetch", map[string]int{"/api/dcim/devices/": http.StatusForbidden}},
		{"401 alongside a 500", map[string]int{"/api/dcim/devices/": http.StatusUnauthorized, "/api/dcim/cables/": http.StatusInternalServerError}},
		{"404 on a collection fetch", map[string]int{"/api/dcim/devices/": http.StatusNotFound}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var changeReads atomic.Int32
			client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/core/object-changes/" {
					changeReads.Add(1)
				}
				if status, ok := tc.fail[r.URL.Path]; ok {
					http.Error(w, "no", status)
					return
				}
				_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
			}))
			obs := newRecordingObserver()

			_, err := LoadConsistentSnapshot(context.Background(), client, 5, time.Millisecond, obs)
			var httpErr *HTTPError
			if !errors.As(err, &httpErr) {
				t.Fatalf("err = %v, want an *HTTPError", err)
			}
			if got := len(obs.loadErrs); got != 1 {
				t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
			}
			if len(obs.delays) != 0 {
				t.Errorf("retry delays %v reported, want none", obs.delays)
			}
			if got := changeReads.Load(); got != 1 {
				t.Errorf("change read %d times, want 1 (attempt 1's start read only)", got)
			}
		})
	}
}

// TestLoadConsistentSnapshotMalformedURLIsNotRetried covers a failure that
// happens before any request is sent.
func TestLoadConsistentSnapshotMalformedURLIsNotRetried(t *testing.T) {
	client := &Client{BaseURL: "http://[::1", Token: "x", HTTPClient: http.DefaultClient}
	obs := newRecordingObserver()

	if _, err := LoadConsistentSnapshot(context.Background(), client, 5, time.Millisecond, obs); err == nil {
		t.Fatal("expected error, got nil")
	}
	if got := len(obs.loadErrs); got != 1 {
		t.Errorf("SnapshotLoadError fired %d times, want 1: %v", got, obs.loadErrs)
	}
	if len(obs.delays) != 0 {
		t.Errorf("retry delays %v reported, want none", obs.delays)
	}
}

// TestLoadConsistentSnapshotRetriesThrottling checks that 408 and 429, the
// 4xx statuses that mean "try again later", are retried.
func TestLoadConsistentSnapshotRetriesThrottling(t *testing.T) {
	for _, status := range []int{http.StatusRequestTimeout, http.StatusTooManyRequests} {
		t.Run(http.StatusText(status), func(t *testing.T) {
			var changeReads atomic.Int32
			client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/api/core/object-changes/" && changeReads.Add(1) == 1 {
					http.Error(w, "later", status)
					return
				}
				_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
			}))

			snap, err := LoadConsistentSnapshot(context.Background(), client, 2, time.Millisecond, nil)
			if err != nil {
				t.Fatalf("LoadConsistentSnapshot: %v", err)
			}
			if snap.SnapshotAttempts != 2 {
				t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
			}
		})
	}
}

// TestLoadConsistentSnapshotRetriesTransportFailure drops the connection on the
// first change read, so the client sees a transport error rather than a status.
func TestLoadConsistentSnapshotRetriesTransportFailure(t *testing.T) {
	var changeReads atomic.Int32
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/core/object-changes/" && changeReads.Add(1) == 1 {
			conn, _, err := http.NewResponseController(w).Hijack()
			if err != nil {
				t.Errorf("hijack: %v", err)
				return
			}
			_ = conn.Close()
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))

	snap, err := LoadConsistentSnapshot(context.Background(), client, 2, time.Millisecond, nil)
	if err != nil {
		t.Fatalf("LoadConsistentSnapshot: %v", err)
	}
	if snap.SnapshotAttempts != 2 {
		t.Errorf("SnapshotAttempts=%d, want 2", snap.SnapshotAttempts)
	}
}

// TestLoadConsistentSnapshotCancelledRetryKeepsCause fails attempt 1 with a 500
// and cancels inside a fetch on attempt 2; the error must carry both.
func TestLoadConsistentSnapshotCancelledRetryKeepsCause(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var cableReads, deviceReads atomic.Int32
	client := newTestClient(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/dcim/cables/" && cableReads.Add(1) == 1:
			http.Error(w, "boom", http.StatusInternalServerError)
			return
		case r.URL.Path == "/api/dcim/devices/" && deviceReads.Add(1) == 2:
			cancel()
			http.Error(w, "cancelled", http.StatusInternalServerError)
			return
		}
		_, _ = w.Write([]byte(`{"count":0,"next":null,"results":[]}`))
	}))

	_, err := LoadConsistentSnapshot(ctx, client, 3, time.Millisecond, nil)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("err = %v, want context.Canceled", err)
	}
	var httpErr *HTTPError
	if !errors.As(err, &httpErr) || httpErr.StatusCode != http.StatusInternalServerError {
		t.Errorf("err = %v, want it to keep attempt 1's HTTP 500", err)
	}
}
