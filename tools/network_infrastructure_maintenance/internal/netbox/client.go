package netbox

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
)

const DefaultPageSize = 1000

const (
	protocolHTTP        = "http://"
	protocolHTTPS       = "https://"
	schemeHTTP          = "http"
	schemeHTTPS         = "https"
	queryLimit          = "limit"
	headerAuthorization = "Authorization"
	headerAccept        = "Accept"
	headerCacheControl  = "Cache-Control"
	headerPragma        = "Pragma"
	headerValueNoCache  = "no-cache"
	acceptApplicationJS = "application/json"
	tokenPrefix         = "Token "
)

type Client struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// HTTPError is a non-2xx response from NetBox.
type HTTPError struct {
	StatusCode int
	Body       string
}

func (e *HTTPError) Error() string {
	return fmt.Sprintf("HTTP %d: %s", e.StatusCode, e.Body)
}

// transientError marks a failure to reach NetBox or read its response, which
// a later request need not repeat. It carries the wrapped error's message.
type transientError struct{ err error }

func (e transientError) Error() string { return e.err.Error() }
func (e transientError) Unwrap() error { return e.err }

type ObjectChange struct {
	ID      int    `json:"id"`
	Display string `json:"display"`
}

type page[T any] struct {
	Count   int     `json:"count"`
	Next    *string `json:"next"`
	Results []T     `json:"results"`
}

func (c *Client) LatestChange(ctx context.Context) (ObjectChange, error) {
	urlStr, err := c.ResolveURL("/api/core/object-changes/?limit=1")
	if err != nil {
		return ObjectChange{}, err
	}
	body, err := c.DoRequest(ctx, urlStr)
	if err != nil {
		return ObjectChange{}, err
	}
	var p page[ObjectChange]
	if err := json.Unmarshal(body, &p); err != nil {
		return ObjectChange{}, err
	}
	if len(p.Results) == 0 {
		return ObjectChange{}, nil
	}
	return p.Results[0], nil
}

// PageProgressFunc is invoked after each page response during a paginated
// fetch. totalCount is NetBox's "count" field (the authoritative total item
// count, repeated on every page), itemsSoFar is the count appended to the
// caller's slice so far, and requestsSoFar is the number of HTTP requests
// completed so far.
type PageProgressFunc func(itemsSoFar, totalCount, requestsSoFar int)

func FetchAll[T any](
	ctx context.Context,
	client *Client,
	path string,
) (items []T, requests, pages int, err error) {
	return FetchAllWithProgress[T](ctx, client, path, nil)
}

func FetchAllWithProgress[T any](
	ctx context.Context,
	client *Client,
	path string,
	progress PageProgressFunc,
) (items []T, requests, pages int, err error) {
	var urlStr string
	urlStr, err = client.ResolveURL(path)
	if err != nil {
		return
	}
	for urlStr != "" {
		var body []byte
		body, err = client.DoRequest(ctx, urlStr)
		if err != nil {
			return
		}
		requests++
		pages++
		var p page[T]
		if err = json.Unmarshal(body, &p); err != nil {
			return
		}
		items = append(items, p.Results...)
		if progress != nil {
			progress(len(items), p.Count, requests)
		}
		if p.Next != nil {
			urlStr = *p.Next
		} else {
			urlStr = ""
		}
	}
	return
}

// ResolveURL resolves path against BaseURL (or takes it as-is if absolute)
// and adds the default page size. It rejects a URL without an http or https
// scheme and a host, which url.Parse accepts (a missing "http://" makes the
// host the scheme) but no request could succeed against.
func (c *Client) ResolveURL(path string) (string, error) {
	raw := path
	if !strings.HasPrefix(path, protocolHTTP) && !strings.HasPrefix(path, protocolHTTPS) {
		raw = strings.TrimRight(c.BaseURL, "/") + path
	}
	u, err := url.Parse(raw)
	if err != nil {
		return "", err
	}
	if (u.Scheme != schemeHTTP && u.Scheme != schemeHTTPS) || u.Host == "" {
		return "", fmt.Errorf("URL %q needs an http:// or https:// scheme and a host", raw)
	}
	q := u.Query()
	if q.Get(queryLimit) == "" {
		q.Set(queryLimit, fmt.Sprintf("%d", DefaultPageSize))
		u.RawQuery = q.Encode()
	}
	return u.String(), nil
}

func (c *Client) DoRequest(ctx context.Context, urlStr string) ([]byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, urlStr, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set(headerAuthorization, tokenPrefix+c.Token)
	req.Header.Set(headerAccept, acceptApplicationJS)
	req.Header.Set(headerCacheControl, headerValueNoCache)
	req.Header.Set(headerPragma, headerValueNoCache)
	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return nil, transientError{err}
	}
	defer func() { _ = resp.Body.Close() }()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, transientError{err}
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return nil, &HTTPError{StatusCode: resp.StatusCode, Body: strings.TrimSpace(string(body))}
	}
	return body, nil
}
