package main

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"io/fs"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

//go:embed site
var siteFS embed.FS

// payloadSet caches pre-compressed payload bodies in memory,
// keyed by "<name>.<encoding>", refreshed from Postgres when built_at moves.
type payloadSet struct {
	mu      sync.RWMutex
	builtAt string
	bodies  map[string][]byte
}

func (p *payloadSet) get(name, encoding string) ([]byte, string) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.bodies[name+"."+encoding], p.builtAt
}

func (p *payloadSet) refresh(db *sql.DB) error {
	var latest string
	if err := db.QueryRow("SELECT max(built_at)::text FROM payloads").Scan(&latest); err != nil {
		return err
	}
	p.mu.RLock()
	current := p.builtAt
	p.mu.RUnlock()
	if latest == current {
		return nil
	}
	rows, err := db.Query("SELECT name, encoding, body FROM payloads")
	if err != nil {
		return err
	}
	defer rows.Close()
	bodies := map[string][]byte{}
	for rows.Next() {
		var name, encoding string
		var body []byte
		if err := rows.Scan(&name, &encoding, &body); err != nil {
			return err
		}
		bodies[name+"."+encoding] = body
	}
	if err := rows.Err(); err != nil {
		return err
	}
	p.mu.Lock()
	p.builtAt = latest
	p.bodies = bodies
	p.mu.Unlock()
	log.Printf("payloads loaded, built_at %s", latest)
	return nil
}

func servePayload(p *payloadSet) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		name := strings.TrimSuffix(path.Base(r.URL.Path), ".bin")
		encoding := "gzip"
		body, builtAt := p.get(name, "gz")
		if strings.Contains(r.Header.Get("Accept-Encoding"), "br") {
			if br, _ := p.get(name, "br"); br != nil {
				body, encoding = br, "br"
			}
		}
		if body == nil {
			http.NotFound(w, r)
			return
		}
		// Unversioned URLs; built_at as ETag lets clients revalidate cheaply.
		etag := `"` + builtAt + `"`
		w.Header().Set("Cache-Control", "no-cache")
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Vary", "Accept-Encoding")
		w.Header().Set("Content-Encoding", encoding)
		w.Header().Set("ETag", etag)
		if r.Header.Get("If-None-Match") == etag {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		w.Write(body)
	}
}

func handler(static http.Handler, staticFS fs.FS, payloads *payloadSet) http.HandlerFunc {
	binHandler := servePayload(payloads)
	return func(w http.ResponseWriter, r *http.Request) {
		p := r.URL.Path
		switch {
		case strings.HasSuffix(p, "/data.bin") || strings.HasSuffix(p, "/rest.bin"):
			binHandler(w, r)
			return
		case strings.HasSuffix(p, ".css") || strings.HasSuffix(p, ".js"):
			w.Header().Set("Cache-Control", "no-cache")
		default:
			w.Header().Set("Cache-Control", "no-cache")
		}
		// SPA routing: extensionless paths get index.html.
		if p != "/" && path.Ext(p) == "" {
			index, err := fs.ReadFile(staticFS, "index.html")
			if err != nil {
				http.NotFound(w, r)
				return
			}
			w.Header().Set("Content-Type", "text/html; charset=utf-8")
			w.Write(index)
			return
		}
		static.ServeHTTP(w, r)
	}
}

func main() {
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		log.Fatal("DATABASE_URL is required")
	}
	db, err := sql.Open("pgx", dsn)
	if err != nil {
		log.Fatal(err)
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	payloads := &payloadSet{bodies: map[string][]byte{}}
	if err := payloads.refresh(db); err != nil {
		log.Printf("initial payload load failed: %v", err)
	}
	go func() {
		ticker := time.NewTicker(10 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := payloads.refresh(db); err != nil {
					log.Printf("payload refresh failed: %v", err)
				}
			}
		}
	}()
	sub, err := fs.Sub(siteFS, "site")
	if err != nil {
		log.Fatal(err)
	}
	srv := &http.Server{
		Addr:              ":" + port,
		Handler:           handler(http.FileServerFS(sub), sub, payloads),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      30 * time.Second,
		IdleTimeout:       60 * time.Second,
	}
	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdownCtx); err != nil {
			log.Printf("shutdown: %v", err)
		}
	}()
	log.Printf("serving on :%s", port)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
