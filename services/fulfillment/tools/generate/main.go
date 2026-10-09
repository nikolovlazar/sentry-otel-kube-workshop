// Regenerates committed protobuf code with checksummed Go tools, without protoc.
package main

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/bufbuild/protocompile"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/reflect/protodesc"
	"google.golang.org/protobuf/types/pluginpb"
)

func main() {
	if err := generate(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func generate() error {
	compiler := protocompile.Compiler{Resolver: &protocompile.SourceResolver{ImportPaths: []string{"../../../proto"}}}
	files, err := compiler.Compile(context.Background(), "workshop.proto")
	if err != nil {
		return err
	}
	parameter := "module=github.com/nikolovlazar/sentry-otel-kube-workshop/services/fulfillment"
	request := &pluginpb.CodeGeneratorRequest{FileToGenerate: []string{"workshop.proto"}, Parameter: &parameter}
	for _, file := range files {
		request.ProtoFile = append(request.ProtoFile, protodesc.ToFileDescriptorProto(file))
	}
	input, err := proto.Marshal(request)
	if err != nil {
		return err
	}
	for _, plugin := range []string{"google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.10", "google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.6.0"} {
		command := exec.Command("go", "run", plugin)
		command.Stdin = bytes.NewReader(input)
		command.Stderr = os.Stderr
		output, err := command.Output()
		if err != nil {
			return fmt.Errorf("%s: %w", plugin, err)
		}
		response := &pluginpb.CodeGeneratorResponse{}
		if err := proto.Unmarshal(output, response); err != nil {
			return err
		}
		if response.GetError() != "" {
			return fmt.Errorf("generator: %s", response.GetError())
		}
		for _, file := range response.File {
			path := filepath.Join("..", file.GetName())
			if err := os.MkdirAll(filepath.Dir(path), 0755); err != nil {
				return err
			}
			if err := os.WriteFile(path, []byte(file.GetContent()), 0644); err != nil {
				return err
			}
		}
	}
	return nil
}
