<?php
/**
 * Connector settings (allowlist toggles).
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Reads and sanitizes the `wmhc_settings` option.
 */
class Settings {

	const OPTION = 'wmhc_settings';

	/**
	 * Default values.
	 *
	 * @return array<string, bool>
	 */
	public static function defaults() {
		return array(
			'allow_core_updates'       => true,
			'allow_major_core_updates' => false,
		);
	}

	/**
	 * Returns all settings merged with defaults.
	 *
	 * @return array<string, bool>
	 */
	public function all() {
		$stored = get_option( self::OPTION, array() );
		if ( ! is_array( $stored ) ) {
			$stored = array();
		}
		return self::sanitize( wp_parse_args( $stored, self::defaults() ) );
	}

	/**
	 * Returns a single boolean setting.
	 *
	 * @param string $key Setting key.
	 * @return bool
	 */
	public function get( $key ) {
		$all = $this->all();
		return ! empty( $all[ $key ] );
	}

	/**
	 * Sanitizes a settings array (also used as the register_setting callback).
	 *
	 * Missing checkbox keys mean "off", so this must only be fed complete form
	 * submissions or values already merged with defaults.
	 *
	 * @param mixed $value Raw value.
	 * @return array<string, bool>
	 */
	public static function sanitize( $value ) {
		$value = is_array( $value ) ? $value : array();
		$clean = array();
		foreach ( array_keys( self::defaults() ) as $key ) {
			$clean[ $key ] = ! empty( $value[ $key ] ) && 'false' !== $value[ $key ] && '0' !== $value[ $key ];
		}
		return $clean;
	}
}
