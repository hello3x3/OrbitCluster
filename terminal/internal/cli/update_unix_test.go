//go:build !windows

package cli

import "testing"

func holdUpdateTestLauncher(t *testing.T, path string) func() { return func() {} }
