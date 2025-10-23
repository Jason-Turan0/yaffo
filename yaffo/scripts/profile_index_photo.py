import cProfile
import pstats
import io
import json
import time
import tracemalloc
import subprocess
import sys
from glob import glob
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from yaffo.utils.index_photos import index_photo
profile_name = datetime.now().strftime("%Y%m%d_%H%M%S")

PROFILE_DIR = Path("./yaffo/scripts/performance_profiles")
RESULTS_DIR = PROFILE_DIR / "results"
PROFILE_RUNS_DIR = RESULTS_DIR / profile_name
THUMBNAIL_DIR = PROFILE_RUNS_DIR / "thumbnails"
PROFILE_HISTORY_FILE = PROFILE_DIR / "profile_history.json"
TEST_DATA_DIR = Path("./yaffo/scripts/test_data/samples")

def ensure_profile_directory() -> None:
    PROFILE_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)
    PROFILE_RUNS_DIR.mkdir(exist_ok=True)
    THUMBNAIL_DIR.mkdir(exist_ok=True)


def profile_index_photo_task(
        photo_paths: List[str]
) -> Dict:
    """
    Profile the index_photo_task with the given photo paths.

    Returns:
        Dictionary containing profiling results and metrics
    """
    ensure_profile_directory()

    print(f"\n{'=' * 80}")
    print(f"Performance Profile: {profile_name}")
    print(f"{'=' * 80}")
    print(f"Photos to process: {len(photo_paths)}")

    profiler = cProfile.Profile()
    tracemalloc.start()

    start_time = time.time()
    start_memory = tracemalloc.get_traced_memory()[0]

    index_results_list = []
    errors = 0
    profiler.enable()
    for photo_path in photo_paths:
        result = index_photo(Path(photo_path), THUMBNAIL_DIR)
        if result is None:
            errors += 1
        index_results_list.append({
            'photo_path': photo_path,
            'result': result
        })

    profiler.disable()

    end_time = time.time()
    current_memory, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    elapsed_time = end_time - start_time
    memory_used = (peak_memory - start_memory) / 1024 / 1024

    photos_indexed = len([r for r in index_results_list if r['result'] is not None])
    total_faces = sum(len(r['result'].get('faces_data', [])) for r in index_results_list if r['result'] is not None)

    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(30)
    profile_output = s.getvalue()

    prof_file = PROFILE_DIR / f"{profile_name}.prof"
    profiler.dump_stats(str(prof_file))

    results_data_for_save = []
    for item in index_results_list:
        result_copy = {
            'photo_path': item['photo_path'],
            'result': None if item['result'] is None else {
                'latitude': item['result'].get('latitude'),
                'longitude': item['result'].get('longitude'),
                'location_name': item['result'].get('location_name'),
                'tags': item['result'].get('tags', []),
                'num_faces': len(item['result'].get('faces_data', [])),
                'face_locations': [
                    {
                        'top': face['location_top'],
                        'right': face['location_right'],
                        'bottom': face['location_bottom'],
                        'left': face['location_left'],
                    }
                    for face in item['result'].get('faces_data', [])
                ]
            }
        }
        results_data_for_save.append(result_copy)

    results = {
        "profile_name": profile_name,
        "timestamp": datetime.now().isoformat(),
        "metrics": {
            "total_photos": len(photo_paths),
            "photos_indexed": photos_indexed,
            "errors": errors,
            "elapsed_time_seconds": round(elapsed_time, 3),
            "time_per_photo_seconds": round(elapsed_time / len(photo_paths), 3) if photo_paths else 0,
            "photos_per_second": round(len(photo_paths) / elapsed_time, 3) if elapsed_time > 0 else 0,
            "peak_memory_mb": round(memory_used, 2),
            "memory_per_photo_mb": round(memory_used / len(photo_paths), 2) if photo_paths else 0,
            "faces_detected": total_faces,
            "faces_per_photo": round(total_faces / photos_indexed, 2) if photos_indexed > 0 else 0,
        },
        "profile_stats": profile_output,
        "index_results": results_data_for_save
    }

    profile_file = PROFILE_RUNS_DIR / f"{profile_name}_profile.json"
    with open(profile_file, 'w') as f:
        json.dump(results, f, indent=2)

    results_data_file = PROFILE_RUNS_DIR / f"{profile_name}_results.json"
    with open(results_data_file, 'w') as f:
        json.dump(results_data_for_save, f, indent=2)

    print(f"\n{'=' * 80}")
    print("PERFORMANCE METRICS")
    print(f"{'=' * 80}")
    print(f"Total Time:          {results['metrics']['elapsed_time_seconds']:.3f}s")
    print(f"Time per Photo:      {results['metrics']['time_per_photo_seconds']:.3f}s")
    print(f"Throughput:          {results['metrics']['photos_per_second']:.3f} photos/sec")
    print(f"Peak Memory:         {results['metrics']['peak_memory_mb']:.2f} MB")
    print(f"Memory per Photo:    {results['metrics']['memory_per_photo_mb']:.2f} MB")
    print(f"Photos Indexed:      {results['metrics']['photos_indexed']}/{results['metrics']['total_photos']}")
    print(f"Errors:              {results['metrics']['errors']}")
    print(f"Faces Detected:      {results['metrics']['faces_detected']}")
    print(f"Faces per Photo:     {results['metrics']['faces_per_photo']:.2f}")
    print(f"\nProfile saved to:      {profile_file}")
    print(f"Results data saved to: {results_data_file}")
    print(f"cProfile data (.prof): {prof_file}")

    print(f"\n{'=' * 80}")
    print("TOP 30 FUNCTIONS BY CUMULATIVE TIME")
    print(f"{'=' * 80}")
    print(profile_output)

    save_to_history(results)

    results["prof_file"] = str(prof_file)
    return results


def save_to_history(results: Dict) -> None:
    """Append results to historical profile data."""
    history = []

    if PROFILE_HISTORY_FILE.exists():
        with open(PROFILE_HISTORY_FILE, 'r') as f:
            history = json.load(f)

    history_entry = {
        "profile_name": results["profile_name"],
        "timestamp": results["timestamp"],
        "metrics": results["metrics"]
    }

    history.append(history_entry)

    with open(PROFILE_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)


def show_performance_summary(limit: int = 10) -> None:
    """Display summary of recent performance profiles."""
    if not PROFILE_HISTORY_FILE.exists():
        print("No performance history found.")
        return

    with open(PROFILE_HISTORY_FILE, 'r') as f:
        history = json.load(f)

    if not history:
        print("No performance history found.")
        return

    recent = history[-limit:]

    print(f"\n{'=' * 80}")
    print(f"PERFORMANCE HISTORY (Last {len(recent)} runs)")
    print(f"{'=' * 80}")
    print(f"{'Date':<20} {'Photos':<8} {'Time':<10} {'Rate':<12} {'Memory':<10} {'Faces':<8}")
    print(f"{'-' * 80}")

    for entry in recent:
        timestamp = datetime.fromisoformat(entry["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
        metrics = entry["metrics"]
        print(
            f"{timestamp:<20} "
            f"{metrics['photos_indexed']:<8} "
            f"{metrics['elapsed_time_seconds']:<10.3f} "
            f"{metrics['photos_per_second']:<12.3f} "
            f"{metrics['peak_memory_mb']:<10.2f} "
            f"{metrics['faces_detected']:<8}"
        )

    avg_time = sum(e["metrics"]["elapsed_time_seconds"] for e in recent) / len(recent)
    avg_rate = sum(e["metrics"]["photos_per_second"] for e in recent) / len(recent)
    avg_memory = sum(e["metrics"]["peak_memory_mb"] for e in recent) / len(recent)

    print(f"{'-' * 80}")
    print(f"{'AVERAGES':<20} "
          f"{'':<8} "
          f"{avg_time:<10.3f} "
          f"{avg_rate:<12.3f} "
          f"{avg_memory:<10.2f}")
    print(f"{'=' * 80}\n")


def launch_snakeviz(prof_file: Path) -> None:
    """Launch snakeviz to visualize the profile data."""
    try:
        print(f"\nLaunching snakeviz for {prof_file.name}...")
        print("Press Ctrl+C to stop the server when done.")
        subprocess.run([sys.executable, "-m", "snakeviz", str(prof_file)])
    except KeyboardInterrupt:
        print("\nsnakeviz stopped.")
    except FileNotFoundError:
        print("\nERROR: snakeviz not found. Install it with:")
        print("  pip install snakeviz")
    except Exception as e:
        print(f"\nERROR launching snakeviz: {e}")


def verify_face_detection_accuracy(
        index_results: List[Dict],
        ground_truth: Dict[str, str]
) -> Dict:
    """
    Verify face detection accuracy against ground truth labels.

    Args:
        index_results: List of index results from profiling
        ground_truth: Dict mapping photo_path -> person_name

    Returns:
        Dictionary with accuracy metrics
    """
    photos_with_faces_detected = 0
    photos_with_faces_expected = len(index_results)
    total_faces_detected = 0

    for result in index_results:
        if result['result'] is not None:
            num_faces = result['result'].get('num_faces', 0)
            if num_faces > 0:
                photos_with_faces_detected += 1
                total_faces_detected += num_faces

    detection_rate = photos_with_faces_detected / photos_with_faces_expected if photos_with_faces_expected > 0 else 0

    accuracy_metrics = {
        "photos_with_faces_expected": photos_with_faces_expected,
        "photos_with_faces_detected": photos_with_faces_detected,
        "detection_rate": round(detection_rate, 3),
        "total_faces_detected": total_faces_detected,
        "avg_faces_per_photo": round(total_faces_detected / photos_with_faces_expected,
                                     2) if photos_with_faces_expected > 0 else 0,
    }

    return accuracy_metrics


def verify_correctness(
        results: Dict,
        ground_truth: Optional[Dict[str, str]] = None
) -> bool:
    """
    Verify that the index_photo_task behaved correctly.

    Args:
        results: Profiling results dictionary
        ground_truth: Optional ground truth labels for face detection verification

    Returns:
        True if all assertions pass, False otherwise
    """
    print(f"\n{'=' * 80}")
    print("CORRECTNESS VERIFICATION")
    print(f"{'=' * 80}")

    assertions = []

    metrics = results["metrics"]
    assertions.append((
        "All photos processed",
        metrics["photos_indexed"] + metrics["errors"] == metrics["total_photos"],
        f"Expected {metrics['total_photos']}, got {metrics['photos_indexed'] + metrics['errors']}"
    ))

    assertions.append((
        "No errors occurred",
        metrics["errors"] == 0,
        f"Expected 0 errors, got {metrics['errors']}"
    ))

    assertions.append((
        "Reasonable processing time",
        metrics["time_per_photo_seconds"] < 10.0,
        f"Processing took {metrics['time_per_photo_seconds']:.3f}s per photo (threshold: 10s)"
    ))

    assertions.append((
        "Reasonable memory usage",
        metrics["memory_per_photo_mb"] < 500,
        f"Used {metrics['memory_per_photo_mb']:.2f}MB per photo (threshold: 500MB)"
    ))

    if ground_truth:
        accuracy = results.get("accuracy_metrics", {})
        if accuracy:
            detection_rate = accuracy.get("detection_rate", 0)

            assertions.append((
                "Face detection rate >= 80%",
                detection_rate >= 0.80,
                f"Detection rate: {detection_rate:.1%} (threshold: 80%)"
            ))

            assertions.append((
                "At least some faces detected",
                accuracy.get("total_faces_detected", 0) > 0,
                f"Detected {accuracy.get('total_faces_detected', 0)} faces"
            ))

    all_passed = True
    for name, passed, message in assertions:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"{status}: {name}")
        if not passed:
            print(f"  {message}")
            all_passed = False

    if ground_truth and results.get("accuracy_metrics"):
        print(f"\n{'=' * 80}")
        print("FACE DETECTION ACCURACY (vs Ground Truth)")
        print(f"{'=' * 80}")
        accuracy = results["accuracy_metrics"]
        print(f"Photos with faces expected:  {accuracy['photos_with_faces_expected']}")
        print(f"Photos with faces detected:  {accuracy['photos_with_faces_detected']}")
        print(f"Detection rate:              {accuracy['detection_rate']:.1%}")
        print(f"Total faces detected:        {accuracy['total_faces_detected']}")
        print(f"Avg faces per photo:         {accuracy['avg_faces_per_photo']:.2f}")

    print(f"{'=' * 80}\n")

    if all_passed:
        print("All correctness checks passed! ✓")
    else:
        print("Some correctness checks failed! ✗")

    return all_passed


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Profile the performance of index_photo_task with optional LFW test data"
    )
    parser.add_argument(
        "--name",
        type=str,
        help="Name for this profile run (default: timestamp)"
    )
    parser.add_argument(
        "--show-history",
        action="store_true",
        help="Show performance history and exit"
    )
    parser.add_argument(
        "--history-limit",
        type=int,
        default=10,
        help="Number of historical runs to display (default: 10)"
    )
    parser.add_argument(
        "--snakeviz",
        action="store_true",
        help="Launch snakeviz to visualize profile results after profiling",
        default=True
    )

    args = parser.parse_args()

    if args.show_history:
        show_performance_summary(args.history_limit)
        return

    ground_truth = None

    print("\n" + "=" * 80)
    print("=" * 80)

    photo_paths = glob(str(TEST_DATA_DIR / "*"))

    print(f"\nLFW Test Data Summary:")
    print(f"  Total photos: {len(photo_paths)}")

    for i, path in enumerate(photo_paths[:5], 1):
        print(f"  {i}. {Path(path).name}")
    if len(photo_paths) > 5:
        print(f"  ... and {len(photo_paths) - 5} more")

    results = profile_index_photo_task(photo_paths)
    #
    # if ground_truth:
    #     print(f"\n{'='*80}")
    #     print("COMPUTING FACE DETECTION ACCURACY")
    #     print(f"{'='*80}")
    #
    #     accuracy_metrics = verify_face_detection_accuracy(
    #         results["index_results"],
    #         ground_truth
    #     )
    #     results["accuracy_metrics"] = accuracy_metrics
    #
    # verify_correctness(results, ground_truth)

    if args.snakeviz:
        prof_file = Path(results["prof_file"])
        launch_snakeviz(prof_file)

    print("\nUsage Examples:")
    print("  View performance history:")
    print("    inv profile-index-photos --show-history")
    print("\n  Profile with snakeviz visualization:")
    print("    inv profile-index-photos --photos 20 --snakeviz")
    print("\n  Manually view a previous profile:")
    print("    snakeviz yaffo/scripts/performance_profiles/TIMESTAMP.prof")
    print("\nOther profiling tools:")
    print("  - py-spy: Low-overhead sampling profiler (pip install py-spy)")
    print("  - line_profiler: Line-by-line profiling (@profile decorator)")
    print("  - memory_profiler: Memory usage profiling (@profile decorator)")
    print("  - scalene: CPU+GPU+memory profiler (pip install scalene)")


if __name__ == "__main__":
    main()
