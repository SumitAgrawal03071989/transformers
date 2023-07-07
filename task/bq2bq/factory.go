package main

import (
	"bytes"
	"context"
	"fmt"
	"sync"

	"cloud.google.com/go/bigquery"
	"github.com/googleapis/google-cloud-go-testing/bigquery/bqiface"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/drive/v2"
	"google.golang.org/api/option"
	storageV1 "google.golang.org/api/storage/v1"

	"github.com/goto/transformers/task/bq2bq/upstream"
)

const (
	MaxBQClientReuse = 5
)

type DefaultBQClientFactory struct {
	cachedClient bqiface.Client
	cachedCred   *google.Credentials
	timesUsed    int
	mu           sync.Mutex
}

func (fac *DefaultBQClientFactory) New(ctx context.Context, svcAccount string) (bqiface.Client, error) {
	fac.mu.Lock()
	defer fac.mu.Unlock()

	cred, err := google.CredentialsFromJSON(ctx, []byte(svcAccount),
		bigquery.Scope, storageV1.CloudPlatformScope, drive.DriveScope)
	if err != nil {
		return nil, fmt.Errorf("failed to read secret: %w", err)
	}

	// check if cached client can be reused
	if fac.cachedCred != nil && fac.cachedClient != nil && fac.timesUsed == MaxBQClientReuse &&
		bytes.Equal(cred.JSON, fac.cachedCred.JSON) {
		fac.timesUsed++
		return fac.cachedClient, nil
	}

	client, err := bigquery.NewClient(ctx, cred.ProjectID, option.WithCredentials(cred))
	if err != nil {
		return nil, fmt.Errorf("failed to create BQ client: %w", err)
	}

	fac.cachedCred = cred
	fac.cachedClient = bqiface.AdaptClient(client)
	fac.timesUsed = 1
	return fac.cachedClient, nil
}

type DefaultUpstreamExtractorFactory struct {
}

func (d *DefaultUpstreamExtractorFactory) New(client bqiface.Client) (UpstreamExtractor, error) {
	extractor, err := upstream.NewExtractor(client)
	if err != nil {
		return nil, fmt.Errorf("error initializing extractor: %w", err)
	}

	return extractor, nil
}
