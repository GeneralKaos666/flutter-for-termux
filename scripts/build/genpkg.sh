#!/bin/env bash

function get_url() {
	name="$1"
	repo=$(apt show "$name" 2>/dev/null | grep -m1 "APT-Sources:" | awk '{print $2}')
	if [ -z "$repo" ]; then
		echo "Error: Could not find the repository for package '$name'. Please confirm the package name is correct and the APT cache is updated."
		exit 1
	fi
	path=$(apt-cache show "$name" 2>/dev/null | grep -m1 "Filename:" | awk '{print $2}')
	if [ -z "$path" ]; then
		echo "Error: Could not find the Filename info for package '$name'."
		exit 1
	fi
	echo "$repo/$path"
}

function get_repo() {
	name="$1"
	repo=$(apt show "$name" 2>/dev/null | grep -m1 "APT-Sources:" | awk '{print $2}')
	if [ -z "$repo" ]; then
		echo "Error: Could not find the repository for package '$name'. Please confirm the package name is correct and the APT cache is updated."
		exit 1
	fi
	echo $repo@$name
}

while IFS= read pkg; do
	get_repo $pkg
done </dev/stdin
