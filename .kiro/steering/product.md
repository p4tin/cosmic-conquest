# Cosmic Conquest — Product Overview

A retro 1980s-styled, turn-based strategy game playable in the browser.

## Concept

The player pilots a lone flagship across a procedurally-generated 20×10 galaxy grid. The goal is to colonize neutral worlds, fight off Cylon raiders, build infrastructure, and liberate every Cylon base before the Hunter-Killer destroys you.

## Authentication

Players log in with their email address via a one-time password (OTP) flow. No passwords are stored. The OTP is sent via Gmail and is valid for 5 minutes. After verification, a 24-hour session token is issued. Game state persists for 7 days under the player's email, so progress survives across sessions and devices.

## Win / Loss

- **Win**: Reduce all Cylon-owned sectors to zero.
- **Lose**: Ships or hull drop to zero.

## Core Gameplay Loop

Each keypress triggers one action (move, fight, colonize, build, scan, etc.) which calls a REST endpoint, gets back the new `GameState`, and re-renders the map.

## Difficulty

Three tiers (EASY / MEDIUM / HARD) tune Cylon aggression (`expansion_threshold`, `expansion_cost`) and the Hunter-Killer spawn turn.

## Aesthetic

All text is uppercase, CRT-styled. Log messages should feel like a military terminal. Keep the tone terse and dramatic. The login screen matches this aesthetic.
