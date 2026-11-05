<?php
/**
 * Capped action log stored in a non-autoloaded option.
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Records the last connector actions.
 */
class Action_Log {

	const OPTION      = 'wmhc_log';
	const MAX_ENTRIES = 50;

	/**
	 * Appends an entry. Never store secrets in $items or $result.
	 *
	 * @param string $action Action identifier, e.g. "updates.run".
	 * @param array  $items  Items acted on (plugin files, stylesheets, versions...).
	 * @param string $result Result summary: success|partial|failed|error.
	 * @param array  $extra  Optional extra data (source, type, summary...).
	 * @return void
	 */
	public function add( $action, array $items, $result, array $extra = array() ) {
		$user    = wp_get_current_user();
		$entries = $this->all();

		$entry = array(
			'time'       => gmdate( 'c' ),
			'timestamp'  => time(),
			'user_id'    => (int) $user->ID,
			'user_login' => $user->exists() ? $user->user_login : '',
			'action'     => (string) preg_replace( '/[^a-z0-9_.\-]/', '', strtolower( (string) $action ) ),
			'items'      => array_values( array_map( 'strval', $items ) ),
			'result'     => sanitize_key( $result ),
		);
		foreach ( $extra as $key => $value ) {
			if ( ! isset( $entry[ $key ] ) ) {
				$entry[ sanitize_key( $key ) ] = $value;
			}
		}

		array_unshift( $entries, $entry );
		$entries = array_slice( $entries, 0, self::MAX_ENTRIES );

		// Autoload off: the log is only read on demand.
		update_option( self::OPTION, $entries, false );
	}

	/**
	 * Returns entries, newest first.
	 *
	 * @param int $limit Max entries.
	 * @return array<int, array<string, mixed>>
	 */
	public function all( $limit = self::MAX_ENTRIES ) {
		$entries = get_option( self::OPTION, array() );
		if ( ! is_array( $entries ) ) {
			return array();
		}
		return array_slice( array_values( $entries ), 0, max( 0, (int) $limit ) );
	}
}
