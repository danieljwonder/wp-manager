<?php
/**
 * Syntax-checks every PHP file of the connector inside PHP-wasm.
 *
 * Usage (from repo root): e2e/lint.sh  (runs this under PHP 8.3 and 7.4)
 * Uses token_get_all(TOKEN_PARSE) (full parse, throws ParseError) and,
 * when available, opcache_compile_file() (compile without executing).
 */

$root   = $argv[1] ?? '/connector';
$failed = 0;
$count  = 0;
$it     = new RecursiveIteratorIterator( new RecursiveDirectoryIterator( $root, FilesystemIterator::SKIP_DOTS ) );
foreach ( $it as $file ) {
	if ( 'php' !== strtolower( $file->getExtension() ) ) {
		continue;
	}
	++$count;
	$path = $file->getPathname();
	try {
		token_get_all( file_get_contents( $path ), TOKEN_PARSE );
		if ( function_exists( 'opcache_compile_file' ) ) {
			@opcache_compile_file( $path ); // phpcs:ignore
			$err = error_get_last();
			if ( $err && false !== strpos( (string) $err['file'], $path ) && in_array( $err['type'], array( E_COMPILE_ERROR, E_PARSE, E_ERROR ), true ) ) {
				throw new Exception( $err['message'] . ' line ' . $err['line'] );
			}
		}
		echo 'OK   ', substr( $path, strlen( $root ) + 1 ), "\n";
	} catch ( Throwable $e ) {
		++$failed;
		echo 'FAIL ', substr( $path, strlen( $root ) + 1 ), ': ', $e->getMessage(), ' (line ', $e->getLine(), ")\n";
	}
}
echo 'PHP ', PHP_VERSION, ': ', $count, ' files, ', $failed, " failed\n";
exit( $failed ? 1 : 0 );
