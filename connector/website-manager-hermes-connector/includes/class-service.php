<?php
/**
 * Internal service shared by REST routes, abilities and the admin page.
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

use WP_Error;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * All connector operations live here.
 */
class Service {

	const UPDATE_TYPES          = array( 'plugin', 'theme', 'core', 'translations' );
	const LOCK_NAME             = 'wmhc_updates';
	const LOCK_TIMEOUT          = 900;
	const APP_PASSWORD_NAME     = 'Hermes (website-manager)';
	const CONNECTION_PREFIX     = 'wpmh1:';
	const MAX_OUTPUT_CHARS      = 8000;
	const CRON_OVERDUE_SECONDS  = 3600;
	const TIME_LIMIT_SECONDS    = 300;
	const MAX_MESSAGES_PER_ITEM = 20;

	/**
	 * Action log.
	 *
	 * @var Action_Log
	 */
	private $log;

	/**
	 * Settings.
	 *
	 * @var Settings
	 */
	private $settings;

	/**
	 * Errors from api.wordpress.org requests seen during a refresh.
	 *
	 * @var array<int, array<string, string>>
	 */
	private $http_errors = array();

	/**
	 * Constructor.
	 *
	 * @param Action_Log $log      Action log.
	 * @param Settings   $settings Settings.
	 */
	public function __construct( Action_Log $log, Settings $settings ) {
		$this->log      = $log;
		$this->settings = $settings;
	}

	/**
	 * Settings accessor.
	 *
	 * @return Settings
	 */
	public function settings() {
		return $this->settings;
	}

	/**
	 * Action log accessor.
	 *
	 * @return Action_Log
	 */
	public function log() {
		return $this->log;
	}

	/*
	 * ---------------------------------------------------------------------
	 * Capabilities
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Whether the current user may read status, updates, health and the log.
	 *
	 * @return bool
	 */
	public function current_user_can_view() {
		return current_user_can( 'update_plugins' ) || current_user_can( 'update_themes' ) || current_user_can( 'update_core' ) || current_user_can( 'manage_options' );
	}

	/**
	 * Capability required to run updates of a type.
	 *
	 * @param string $type Update type.
	 * @return string
	 */
	public static function capability_for_type( $type ) {
		switch ( $type ) {
			case 'plugin':
				return 'update_plugins';
			case 'theme':
				return 'update_themes';
			case 'translations':
				return 'update_languages';
			case 'core':
			default:
				return 'update_core';
		}
	}

	/**
	 * Whether the current user may run updates of a type.
	 *
	 * @param string $type Update type.
	 * @return bool
	 */
	public function current_user_can_update( $type ) {
		if ( ! in_array( $type, self::UPDATE_TYPES, true ) ) {
			return false;
		}
		return current_user_can( self::capability_for_type( $type ) );
	}

	/*
	 * ---------------------------------------------------------------------
	 * Status
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Builds the /status payload.
	 *
	 * @return array<string, mixed>
	 */
	public function get_status() {
		$this->load_admin_includes();
		$theme   = wp_get_theme();
		$updates = $this->get_updates( false );
		$user    = wp_get_current_user();

		return array(
			'connector_version'      => WMHC_VERSION,
			'wp_version'             => $this->wp_version(),
			'php_version'            => PHP_VERSION,
			'multisite'              => is_multisite(),
			'site_url'               => site_url(),
			'home_url'               => home_url(),
			'rest_url'               => rest_url(),
			'site_name'              => wp_specialchars_decode( get_bloginfo( 'name' ), ENT_QUOTES ),
			'environment_type'       => wp_get_environment_type(),
			'debug'                  => array(
				'wp_debug'         => defined( 'WP_DEBUG' ) && WP_DEBUG,
				'wp_debug_log'     => defined( 'WP_DEBUG_LOG' ) && (bool) WP_DEBUG_LOG,
				'wp_debug_display' => defined( 'WP_DEBUG_DISPLAY' ) && WP_DEBUG_DISPLAY,
				'script_debug'     => defined( 'SCRIPT_DEBUG' ) && SCRIPT_DEBUG,
			),
			'active_theme'           => array(
				'name'       => $theme->get( 'Name' ),
				'stylesheet' => $theme->get_stylesheet(),
				'template'   => $theme->get_template(),
				'version'    => $theme->get( 'Version' ),
			),
			'updates'                => $updates['counts'],
			'last_checked'           => $updates['last_checked'],
			'file_mods_allowed'      => wp_is_file_mod_allowed( 'wmhc_run_updates' ),
			'filesystem_method'      => get_filesystem_method(),
			'abilities_api'          => function_exists( 'wp_register_ability' ),
			'application_passwords'  => wp_is_application_passwords_available(),
			'settings'               => $this->settings->all(),
			'user'                   => array(
				'id'           => (int) $user->ID,
				'login'        => $user->user_login,
				'capabilities' => array(
					'update_plugins'   => current_user_can( 'update_plugins' ),
					'update_themes'    => current_user_can( 'update_themes' ),
					'update_core'      => current_user_can( 'update_core' ),
					'update_languages' => current_user_can( 'update_languages' ),
					'manage_options'   => current_user_can( 'manage_options' ),
				),
			),
			'generated_at'           => gmdate( 'c' ),
		);
	}

	/*
	 * ---------------------------------------------------------------------
	 * Available updates
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Lists available updates, optionally forcing a fresh check.
	 *
	 * @param bool $refresh Force wp_version_check() / wp_update_plugins() / wp_update_themes().
	 * @return array<string, mixed>
	 */
	public function get_updates( $refresh = false ) {
		$this->load_admin_includes();

		$refresh_errors = array();
		if ( $refresh ) {
			$refresh_errors = $this->refresh_update_data();
		}

		$core         = $this->list_core_updates();
		$plugins      = $this->list_plugin_updates();
		$themes       = $this->list_theme_updates();
		$translations = $this->list_translation_updates();

		return array(
			'refreshed'      => (bool) $refresh,
			'refresh_errors' => $refresh_errors,
			'last_checked'   => $this->last_checked(),
			'counts'         => array(
				'core'         => count( $core ),
				'plugins'      => count( $plugins ),
				'themes'       => count( $themes ),
				'translations' => count( $translations ),
				'total'        => count( $core ) + count( $plugins ) + count( $themes ) + count( $translations ),
			),
			'core'           => $core,
			'plugins'        => $plugins,
			'themes'         => $themes,
			'translations'   => $translations,
		);
	}

	/**
	 * Forces fresh update checks against WordPress.org (core's own update APIs).
	 *
	 * Rather than deleting the transients (which would lose data if the API is
	 * unreachable), it expires their last_checked stamp so core re-checks.
	 *
	 * @return array<int, array<string, string>> HTTP errors seen while checking.
	 */
	private function refresh_update_data() {
		$this->http_errors = array();
		add_action( 'http_api_debug', array( $this, 'capture_http_error' ), 10, 5 );

		foreach ( array( 'update_plugins', 'update_themes' ) as $transient ) {
			$current = get_site_transient( $transient );
			if ( is_object( $current ) ) {
				$current->last_checked = 0;
				set_site_transient( $transient, $current );
			}
		}

		wp_version_check( array(), true );
		wp_update_plugins();
		wp_update_themes();

		remove_action( 'http_api_debug', array( $this, 'capture_http_error' ), 10 );
		return $this->http_errors;
	}

	/**
	 * Records failed requests to api.wordpress.org during a refresh.
	 *
	 * @param array|\WP_Error $response HTTP response or error.
	 * @param string          $context  Context.
	 * @param string          $class    Transport class.
	 * @param array           $args     Request args.
	 * @param string          $url      Request URL.
	 * @return void
	 */
	public function capture_http_error( $response, $context, $class, $args, $url ) {
		unset( $context, $class, $args );
		$host = wp_parse_url( $url, PHP_URL_HOST );
		if ( 'api.wordpress.org' !== $host ) {
			return;
		}
		$path = (string) wp_parse_url( $url, PHP_URL_PATH );
		if ( is_wp_error( $response ) ) {
			$this->http_errors[] = array(
				'url'   => 'https://api.wordpress.org' . $path,
				'error' => $response->get_error_message(),
			);
		} else {
			$code = (int) wp_remote_retrieve_response_code( $response );
			if ( 200 !== $code ) {
				$this->http_errors[] = array(
					'url'   => 'https://api.wordpress.org' . $path,
					'error' => 'HTTP ' . $code,
				);
			}
		}
	}

	/**
	 * Last update-check times (ISO 8601, UTC) or null.
	 *
	 * @return array<string, string|null>
	 */
	private function last_checked() {
		$out = array();
		foreach ( array(
			'core'    => 'update_core',
			'plugins' => 'update_plugins',
			'themes'  => 'update_themes',
		) as $key => $transient ) {
			$value       = get_site_transient( $transient );
			$out[ $key ] = ( is_object( $value ) && ! empty( $value->last_checked ) ) ? gmdate( 'c', (int) $value->last_checked ) : null;
		}
		return $out;
	}

	/**
	 * Core update offers.
	 *
	 * @return array<int, array<string, mixed>>
	 */
	private function list_core_updates() {
		$offers = get_core_updates( array( 'dismissed' => true ) );
		if ( ! is_array( $offers ) ) {
			return array();
		}

		$current  = $this->wp_version();
		$disabled = ( new \WP_Automatic_Updater() )->is_disabled();
		$out      = array();

		foreach ( $offers as $offer ) {
			if ( ! isset( $offer->response, $offer->current ) || 'upgrade' !== $offer->response ) {
				continue;
			}
			$is_major = $this->is_major_core_update( $current, $offer->current );
			$out[]    = array(
				'type'                => 'core',
				'name'                => 'WordPress',
				'current_version'     => $current,
				'new_version'         => $offer->current,
				'locale'              => isset( $offer->locale ) ? $offer->locale : get_locale(),
				'is_major'            => $is_major,
				'requires_php'        => isset( $offer->php_version ) ? $offer->php_version : null,
				'requires_mysql'      => isset( $offer->mysql_version ) ? $offer->mysql_version : null,
				'compatible_php'      => isset( $offer->php_version ) ? is_php_version_compatible( $offer->php_version ) : true,
				'allowed'             => $this->settings->get( 'allow_core_updates' ) && ( ! $is_major || $this->settings->get( 'allow_major_core_updates' ) ),
				'auto_update_enabled' => ! $disabled && \Core_Upgrader::should_update_to_version( $offer->current ),
				'dismissed'           => ! empty( $offer->dismissed ),
				'package_available'   => ! empty( $offer->download ),
			);
		}
		return $out;
	}

	/**
	 * Plugin updates.
	 *
	 * @return array<int, array<string, mixed>>
	 */
	private function list_plugin_updates() {
		$updates      = get_plugin_updates();
		$auto_enabled = wp_is_auto_update_enabled_for_type( 'plugin' );
		$auto_list    = (array) get_site_option( 'auto_update_plugins', array() );
		$out          = array();

		foreach ( $updates as $file => $data ) {
			$update       = isset( $data->update ) ? (object) $data->update : new \stdClass();
			$requires     = isset( $update->requires ) ? (string) $update->requires : null;
			$requires_php = isset( $update->requires_php ) ? (string) $update->requires_php : null;

			$out[] = array(
				'type'                => 'plugin',
				'name'                => isset( $data->Name ) ? $data->Name : $file, // phpcs:ignore WordPress.NamingConventions.ValidVariableName.UsedPropertyNotSnakeCase
				'file'                => $file,
				'slug'                => isset( $update->slug ) ? $update->slug : dirname( $file ),
				'current_version'     => isset( $data->Version ) ? $data->Version : null, // phpcs:ignore WordPress.NamingConventions.ValidVariableName.UsedPropertyNotSnakeCase
				'new_version'         => isset( $update->new_version ) ? (string) $update->new_version : null,
				'requires_wp'         => $requires ? $requires : null,
				'requires_php'        => $requires_php ? $requires_php : null,
				'tested_wp'           => isset( $update->tested ) && $update->tested ? (string) $update->tested : null,
				'compatible_wp'       => is_wp_version_compatible( $requires ),
				'compatible_php'      => is_php_version_compatible( $requires_php ),
				'active'              => is_plugin_active( $file ),
				'network_active'      => is_multisite() && is_plugin_active_for_network( $file ),
				'auto_update_enabled' => $auto_enabled && in_array( $file, $auto_list, true ),
				'package_available'   => ! empty( $update->package ),
			);
		}
		return $out;
	}

	/**
	 * Theme updates.
	 *
	 * @return array<int, array<string, mixed>>
	 */
	private function list_theme_updates() {
		$updates      = get_theme_updates();
		$auto_enabled = wp_is_auto_update_enabled_for_type( 'theme' );
		$auto_list    = (array) get_site_option( 'auto_update_themes', array() );
		$active       = array( get_stylesheet(), get_template() );
		$out          = array();

		foreach ( $updates as $stylesheet => $theme ) {
			$update       = isset( $theme->update ) ? (array) $theme->update : array();
			$requires     = ! empty( $update['requires'] ) ? (string) $update['requires'] : null;
			$requires_php = ! empty( $update['requires_php'] ) ? (string) $update['requires_php'] : null;

			$out[] = array(
				'type'                => 'theme',
				'name'                => $theme->get( 'Name' ),
				'stylesheet'          => $stylesheet,
				'slug'                => $stylesheet,
				'current_version'     => $theme->get( 'Version' ),
				'new_version'         => isset( $update['new_version'] ) ? (string) $update['new_version'] : null,
				'requires_wp'         => $requires,
				'requires_php'        => $requires_php,
				'compatible_wp'       => is_wp_version_compatible( $requires ),
				'compatible_php'      => is_php_version_compatible( $requires_php ),
				'active'              => in_array( $stylesheet, $active, true ),
				'auto_update_enabled' => $auto_enabled && in_array( $stylesheet, $auto_list, true ),
				'package_available'   => ! empty( $update['package'] ),
			);
		}
		return $out;
	}

	/**
	 * Translation updates.
	 *
	 * @return array<int, array<string, mixed>>
	 */
	private function list_translation_updates() {
		$out = array();
		foreach ( wp_get_translation_updates() as $translation ) {
			$out[] = array(
				'type'                => 'translation',
				'item'                => $this->translation_item_id( $translation ),
				'translation_type'    => isset( $translation->type ) ? $translation->type : null,
				'slug'                => isset( $translation->slug ) ? $translation->slug : 'default',
				'language'            => isset( $translation->language ) ? $translation->language : null,
				'version'             => isset( $translation->version ) ? $translation->version : null,
				'updated'             => isset( $translation->updated ) ? $translation->updated : null,
				'auto_update_enabled' => ! empty( $translation->autoupdate ),
			);
		}
		return $out;
	}

	/**
	 * Stable id for a translation update: "{type}:{slug}:{language}".
	 *
	 * @param object $translation Translation update object.
	 * @return string
	 */
	private function translation_item_id( $translation ) {
		return sprintf(
			'%s:%s:%s',
			isset( $translation->type ) ? $translation->type : 'core',
			isset( $translation->slug ) ? $translation->slug : 'default',
			isset( $translation->language ) ? $translation->language : ''
		);
	}

	/*
	 * ---------------------------------------------------------------------
	 * Running updates
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Runs updates of one type.
	 *
	 * @param string   $type   plugin|theme|core|translations.
	 * @param string[] $items  Plugin files, theme stylesheets, core version, or translation ids. Empty = all.
	 * @param string   $source Where the request came from (rest|ability).
	 * @return array<string, mixed>|WP_Error
	 */
	public function run_updates( $type, array $items = array(), $source = 'rest' ) {
		if ( ! in_array( $type, self::UPDATE_TYPES, true ) ) {
			return new WP_Error( 'wmhc_invalid_type', __( 'Invalid update type.', 'website-manager-hermes-connector' ), array( 'status' => 400 ) );
		}
		if ( ! $this->current_user_can_update( $type ) ) {
			return new WP_Error( 'wmhc_forbidden', __( 'You are not allowed to run this type of update.', 'website-manager-hermes-connector' ), array( 'status' => rest_authorization_required_code() ) );
		}
		if ( ! wp_is_file_mod_allowed( 'wmhc_run_updates' ) ) {
			return new WP_Error( 'wmhc_file_mods_disallowed', __( 'File modifications are disabled on this site (DISALLOW_FILE_MODS).', 'website-manager-hermes-connector' ), array( 'status' => 403 ) );
		}
		if ( 'core' === $type && ! $this->settings->get( 'allow_core_updates' ) ) {
			return new WP_Error( 'wmhc_core_updates_disabled', __( 'Core updates through Hermes are disabled in Tools → Hermes Connector.', 'website-manager-hermes-connector' ), array( 'status' => 403 ) );
		}

		$items = array_values( array_unique( array_filter( array_map( array( $this, 'sanitize_item' ), $items ), 'strlen' ) ) );

		$this->load_admin_includes();
		$this->load_upgrader();

		if ( ! \WP_Upgrader::create_lock( self::LOCK_NAME, self::LOCK_TIMEOUT ) ) {
			return new WP_Error( 'wmhc_locked', __( 'Another update run is in progress. Try again in a few minutes.', 'website-manager-hermes-connector' ), array( 'status' => 409 ) );
		}

		// Observable transient marker, backed by the atomic core option lock above.
		set_transient( 'wmhc_update_lock', time(), self::LOCK_TIMEOUT );

		if ( function_exists( 'set_time_limit' ) ) {
			set_time_limit( self::TIME_LIMIT_SECONDS );
		}
		wp_raise_memory_limit( 'admin' );

		$started = microtime( true );
		$skin    = new Upgrader_Skin();
		$result  = null;

		$buffer_level = ob_get_level();
		ob_start();
		try {
			switch ( $type ) {
				case 'plugin':
					$result = $this->run_plugin_updates( $items, $skin );
					break;
				case 'theme':
					$result = $this->run_theme_updates( $items, $skin );
					break;
				case 'core':
					$result = $this->run_core_update( $items, $skin );
					break;
				case 'translations':
					$result = $this->run_translation_updates( $items, $skin );
					break;
			}
		} catch ( \Throwable $e ) {
			$result = new WP_Error( 'wmhc_exception', $e->getMessage(), array( 'status' => 500 ) );
		} finally {
			$output = '';
			// Quiet skins may leave an inner buffer open if an upgrader throws.
			while ( ob_get_level() > $buffer_level ) {
				$output = (string) ob_get_clean() . $output;
			}
			delete_transient( 'wmhc_update_lock' );
			\WP_Upgrader::release_lock( self::LOCK_NAME );
		}

		if ( is_wp_error( $result ) ) {
			$this->log->add( 'updates.run', $items, 'error', array( 'type' => $type, 'source' => $source, 'error' => $result->get_error_message() ) );
			return $result;
		}

		$results   = $this->normalize_results( $type, $result );
		$succeeded = count( array_filter( wp_list_pluck( $results, 'success' ) ) );
		$overall   = ( 0 === count( $results ) || count( $results ) === $succeeded ) ? 'success' : ( $succeeded > 0 ? 'partial' : 'failed' );

		$global_errors = $skin->get_item_errors( -1 );
		if ( 'success' === $overall && ! empty( $global_errors ) && 0 === count( $results ) ) {
			$overall = 'failed';
		}

		// Upgraders clear the update transients (Language_Pack_Upgrader clears all
		// of them). Repopulate so /updates and /status keep reflecting reality.
		if ( false === get_site_transient( 'update_plugins' ) ) {
			wp_update_plugins();
		}
		if ( false === get_site_transient( 'update_themes' ) ) {
			wp_update_themes();
		}
		if ( false === get_site_transient( 'update_core' ) ) {
			wp_version_check( array(), true );
		}

		$output = trim( (string) preg_replace( "/\n{3,}/", "\n\n", Upgrader_Skin::plain( $output ) ) );
		if ( strlen( $output ) > self::MAX_OUTPUT_CHARS ) {
			$output = substr( $output, 0, self::MAX_OUTPUT_CHARS ) . '…';
		}

		$response = array(
			'type'            => $type,
			'success'         => 'success' === $overall,
			'result'          => $overall,
			'started_at'      => gmdate( 'c', (int) $started ),
			'duration_ms'     => (int) round( ( microtime( true ) - $started ) * 1000 ),
			'results'         => $results,
			'errors'          => $global_errors,
			// Core runs report everything on their single result row.
			'messages'        => 'core' === $type ? array() : array_slice( $skin->get_item_messages( -1 ), 0, self::MAX_MESSAGES_PER_ITEM ),
			'output'          => $output,
			'maintenance_off' => ! file_exists( ABSPATH . '.maintenance' ),
		);

		$this->log->add(
			'updates.run',
			wp_list_pluck( $results, 'item' ),
			$overall,
			array(
				'type'    => $type,
				'source'  => $source,
				'summary' => array_map(
					static function ( $r ) {
						return array(
							'item'         => $r['item'],
							'from_version' => $r['from_version'],
							'to_version'   => $r['to_version'],
							'success'      => $r['success'],
						);
					},
					$results
				),
			)
		);

		return $response;
	}

	/**
	 * Plugin updates through Plugin_Upgrader::bulk_upgrade().
	 *
	 * @param string[]      $items Plugin files. Empty = all with updates.
	 * @param Upgrader_Skin $skin  Skin.
	 * @return array<int, array<string, mixed>>
	 */
	private function run_plugin_updates( array $items, Upgrader_Skin $skin ) {
		$installed = get_plugins();
		$current   = get_site_transient( 'update_plugins' );
		$available = ( is_object( $current ) && ! empty( $current->response ) ) ? array_keys( (array) $current->response ) : array();

		if ( empty( $items ) ) {
			$items = array_values( array_intersect( $available, array_keys( $installed ) ) );
		}

		$results = array();
		$to_run  = array();
		$before  = array();
		foreach ( $items as $file ) {
			if ( 0 !== validate_file( $file ) || ! isset( $installed[ $file ] ) ) {
				$results[ $file ] = $this->item_result( $file, null, null, false, 'not_installed', __( 'Plugin is not installed.', 'website-manager-hermes-connector' ) );
				continue;
			}
			$to_run[]        = $file;
			$before[ $file ] = array(
				'version' => $installed[ $file ]['Version'],
				'active'  => is_plugin_active( $file ),
				'network' => is_multisite() && is_plugin_active_for_network( $file ),
				'name'    => $installed[ $file ]['Name'],
			);
		}

		if ( empty( $to_run ) ) {
			return array_values( $results );
		}

		$upgrader = new \Plugin_Upgrader( $skin );
		$raw      = $upgrader->bulk_upgrade( $to_run );

		wp_cache_delete( 'plugins', 'plugins' );
		foreach ( $to_run as $index => $file ) {
			$path        = WP_PLUGIN_DIR . '/' . $file;
			$data        = file_exists( $path ) ? get_plugin_data( $path, false, false ) : array();
			$to_version  = isset( $data['Version'] ) ? $data['Version'] : null;
			$item_raw    = is_array( $raw ) && array_key_exists( $file, $raw ) ? $raw[ $file ] : null;
			$errors      = $skin->get_item_errors( $index );
			$entry       = $this->classify_result( $file, $item_raw, is_array( $raw ) && ! array_key_exists( $file, $raw ), $errors, $skin->get_item_errors( -1 ) );
			$active_now  = is_plugin_active( $file );
			$reactivated = null;

			if ( $before[ $file ]['active'] && ! $active_now && file_exists( $path ) ) {
				$activation  = activate_plugin( $file, '', $before[ $file ]['network'], true );
				$reactivated = ! is_wp_error( $activation );
				$active_now  = is_plugin_active( $file );
				if ( is_wp_error( $activation ) ) {
					$entry['success'] = false;
					$entry['error']   = trim( $entry['error'] . ' ' . sprintf( /* translators: %s: error message */ __( 'Reactivation failed: %s', 'website-manager-hermes-connector' ), $activation->get_error_message() ) );
				}
			}

			$results[ $file ] = array_merge(
				$entry,
				array(
					'name'          => $before[ $file ]['name'],
					'from_version'  => $before[ $file ]['version'],
					'to_version'    => $to_version,
					'active_before' => $before[ $file ]['active'],
					'active_after'  => $active_now,
					'reactivated'   => $reactivated,
					'messages'      => array_slice( $skin->get_item_messages( $index ), 0, self::MAX_MESSAGES_PER_ITEM ),
				)
			);
		}

		return array_values( $this->order_results( $items, $results ) );
	}

	/**
	 * Theme updates through Theme_Upgrader::bulk_upgrade().
	 *
	 * @param string[]      $items Theme stylesheets. Empty = all with updates.
	 * @param Upgrader_Skin $skin  Skin.
	 * @return array<int, array<string, mixed>>
	 */
	private function run_theme_updates( array $items, Upgrader_Skin $skin ) {
		$current   = get_site_transient( 'update_themes' );
		$available = ( is_object( $current ) && ! empty( $current->response ) ) ? array_keys( (array) $current->response ) : array();

		if ( empty( $items ) ) {
			$items = array();
			foreach ( $available as $stylesheet ) {
				if ( wp_get_theme( $stylesheet )->exists() ) {
					$items[] = $stylesheet;
				}
			}
		}

		$active_stylesheet = get_stylesheet();
		$active_template   = get_template();
		$results           = array();
		$to_run            = array();
		$before            = array();

		foreach ( $items as $stylesheet ) {
			$theme = wp_get_theme( $stylesheet );
			if ( 0 !== validate_file( $stylesheet ) || ! $theme->exists() ) {
				$results[ $stylesheet ] = $this->item_result( $stylesheet, null, null, false, 'not_installed', __( 'Theme is not installed.', 'website-manager-hermes-connector' ) );
				continue;
			}
			$to_run[]              = $stylesheet;
			$before[ $stylesheet ] = array(
				'version' => $theme->get( 'Version' ),
				'name'    => $theme->get( 'Name' ),
				'active'  => in_array( $stylesheet, array( $active_stylesheet, $active_template ), true ),
			);
		}

		if ( empty( $to_run ) ) {
			return array_values( $results );
		}

		$upgrader = new \Theme_Upgrader( $skin );
		$raw      = $upgrader->bulk_upgrade( $to_run );

		foreach ( $to_run as $index => $stylesheet ) {
			$style    = get_theme_root( $stylesheet ) . '/' . $stylesheet . '/style.css';
			$headers  = file_exists( $style ) ? get_file_data( $style, array( 'Version' => 'Version' ), 'theme' ) : array();
			$item_raw = is_array( $raw ) && array_key_exists( $stylesheet, $raw ) ? $raw[ $stylesheet ] : null;
			$entry    = $this->classify_result( $stylesheet, $item_raw, is_array( $raw ) && ! array_key_exists( $stylesheet, $raw ), $skin->get_item_errors( $index ), $skin->get_item_errors( -1 ) );

			$results[ $stylesheet ] = array_merge(
				$entry,
				array(
					'name'          => $before[ $stylesheet ]['name'],
					'from_version'  => $before[ $stylesheet ]['version'],
					'to_version'    => isset( $headers['Version'] ) && '' !== $headers['Version'] ? $headers['Version'] : null,
					'active_before' => $before[ $stylesheet ]['active'],
					'active_after'  => in_array( $stylesheet, array( get_stylesheet(), get_template() ), true ),
					'messages'      => array_slice( $skin->get_item_messages( $index ), 0, self::MAX_MESSAGES_PER_ITEM ),
				)
			);
		}

		return array_values( $this->order_results( $items, $results ) );
	}

	/**
	 * Core update through Core_Upgrader::upgrade().
	 *
	 * @param string[]      $items Optional single target version.
	 * @param Upgrader_Skin $skin  Skin.
	 * @return array<int, array<string, mixed>>|WP_Error
	 */
	private function run_core_update( array $items, Upgrader_Skin $skin ) {
		$current_version = $this->wp_version();
		$offers          = get_core_updates( array( 'dismissed' => true ) );
		$offers          = is_array( $offers ) ? $offers : array();
		$target          = isset( $items[0] ) ? $items[0] : '';
		$allow_major     = $this->settings->get( 'allow_major_core_updates' );
		$chosen          = null;

		foreach ( $offers as $offer ) {
			if ( ! isset( $offer->response, $offer->current ) || 'upgrade' !== $offer->response ) {
				continue;
			}
			if ( '' !== $target ) {
				if ( $offer->current === $target ) {
					$chosen = $offer;
					break;
				}
				continue;
			}
			if ( $this->is_major_core_update( $current_version, $offer->current ) && ! $allow_major ) {
				continue;
			}
			if ( null === $chosen || version_compare( $offer->current, $chosen->current, '>' ) ) {
				$chosen = $offer;
			}
		}

		if ( null === $chosen ) {
			if ( '' !== $target ) {
				return new WP_Error( 'wmhc_no_core_update', __( 'That WordPress version is not offered as an update.', 'website-manager-hermes-connector' ), array( 'status' => 404 ) );
			}
			$blocked = array();
			foreach ( $offers as $offer ) {
				if ( isset( $offer->response, $offer->current ) && 'upgrade' === $offer->response ) {
					$blocked[] = $offer->current;
				}
			}
			if ( $blocked ) {
				$entry             = $this->item_result( 'core', $current_version, $current_version, true, 'skipped', null );
				$entry['name']     = 'WordPress';
				$entry['messages'] = array(
					sprintf(
						/* translators: %s: WordPress version(s) */
						__( 'Major WordPress update available (%s) but major core updates are disabled in Tools → Hermes Connector.', 'website-manager-hermes-connector' ),
						implode( ', ', array_unique( $blocked ) )
					),
				);
				return array( $entry );
			}
			return array( $this->item_result( 'core', $current_version, $current_version, true, 'up_to_date', null ) + array( 'name' => 'WordPress' ) );
		}

		if ( $this->is_major_core_update( $current_version, $chosen->current ) && ! $allow_major ) {
			return new WP_Error( 'wmhc_major_core_updates_disabled', __( 'Major WordPress updates through Hermes are disabled in Tools → Hermes Connector.', 'website-manager-hermes-connector' ), array( 'status' => 403 ) );
		}

		$upgrader = new \Core_Upgrader( $skin );
		$raw      = $upgrader->upgrade( $chosen, array( 'attempt_rollback' => true ) );

		$to_version = $this->wp_version( true );
		$errors     = array_merge( $skin->get_item_errors( -1 ), $skin->get_item_errors( 0 ) );
		$success    = ! is_wp_error( $raw ) && false !== $raw && null !== $raw;
		$error      = null;
		if ( is_wp_error( $raw ) ) {
			$error = $raw->get_error_message();
		} elseif ( ! $success ) {
			$error = $errors ? implode( ' ', $errors ) : __( 'Core update failed.', 'website-manager-hermes-connector' );
		}

		$entry = $this->item_result( 'core', $current_version, $to_version, $success, $success ? 'updated' : 'failed', $error );

		return array(
			$entry + array(
				'name'                => 'WordPress',
				'db_upgrade_required' => $this->db_upgrade_required(),
				'messages'            => array_slice( $skin->get_item_messages( -1 ), 0, self::MAX_MESSAGES_PER_ITEM ),
			),
		);
	}

	/**
	 * Translation updates through Language_Pack_Upgrader::bulk_upgrade().
	 *
	 * @param string[]      $items Translation ids ("type:slug:language") or slugs. Empty = all.
	 * @param Upgrader_Skin $skin  Skin.
	 * @return array<int, array<string, mixed>>
	 */
	private function run_translation_updates( array $items, Upgrader_Skin $skin ) {
		$updates = array();
		foreach ( wp_get_translation_updates() as $translation ) {
			$id   = $this->translation_item_id( $translation );
			$slug = isset( $translation->slug ) ? $translation->slug : 'default';
			if ( empty( $items ) || in_array( $id, $items, true ) || in_array( $slug, $items, true ) ) {
				$updates[] = $translation;
			}
		}

		if ( empty( $updates ) ) {
			return array();
		}

		$upgrader = new \Language_Pack_Upgrader( $skin );
		$raw      = $upgrader->bulk_upgrade( $updates );
		$results  = array();

		foreach ( $updates as $index => $translation ) {
			$item_raw = is_array( $raw ) && array_key_exists( $index, $raw ) ? $raw[ $index ] : null;
			$entry    = $this->classify_result(
				$this->translation_item_id( $translation ),
				$item_raw,
				is_array( $raw ) && ! array_key_exists( $index, $raw ),
				$skin->get_item_errors( $index ),
				$skin->get_item_errors( -1 )
			);
			$entry['name']         = isset( $translation->slug ) ? $translation->slug : 'default';
			$entry['messages']     = array_slice( $skin->get_item_messages( $index ), 0, self::MAX_MESSAGES_PER_ITEM );
			$entry['from_version'] = null;
			$entry['to_version']   = isset( $translation->version ) ? $translation->version : null;
			$entry['language']     = isset( $translation->language ) ? $translation->language : null;
			$entry['updated']      = isset( $translation->updated ) ? $translation->updated : null;
			$results[]             = $entry;
		}

		return $results;
	}

	/**
	 * Turns a raw upgrader result into a result entry.
	 *
	 * @param string   $item          Item id.
	 * @param mixed    $raw           Raw result (true = up to date, array = installed, WP_Error/false/null = failed).
	 * @param bool     $not_attempted Whether the upgrader stopped before this item.
	 * @param string[] $errors        Item errors.
	 * @param string[] $global_errors Errors not tied to an item.
	 * @return array<string, mixed>
	 */
	private function classify_result( $item, $raw, $not_attempted, array $errors, array $global_errors ) {
		if ( true === $raw ) {
			return $this->item_result( $item, null, null, true, 'up_to_date', null );
		}
		if ( is_wp_error( $raw ) ) {
			return $this->item_result( $item, null, null, false, 'failed', $raw->get_error_message() );
		}
		if ( $not_attempted ) {
			return $this->item_result( $item, null, null, false, 'skipped', __( 'Not attempted because an earlier item failed.', 'website-manager-hermes-connector' ) );
		}
		if ( ! empty( $raw ) && empty( $errors ) ) {
			return $this->item_result( $item, null, null, true, 'updated', null );
		}
		$messages = array_merge( $errors, $global_errors );
		$message  = $messages ? implode( ' ', array_unique( $messages ) ) : __( 'Update failed.', 'website-manager-hermes-connector' );
		return $this->item_result( $item, null, null, false, 'failed', $message );
	}

	/**
	 * Builds a result entry with the documented keys.
	 *
	 * @param string      $item         Item id.
	 * @param string|null $from_version Version before.
	 * @param string|null $to_version   Version after.
	 * @param bool        $success      Success flag.
	 * @param string      $status       updated|up_to_date|failed|skipped|not_installed.
	 * @param string|null $error        Error message.
	 * @return array<string, mixed>
	 */
	private function item_result( $item, $from_version, $to_version, $success, $status, $error ) {
		return array(
			'item'         => $item,
			'from_version' => $from_version,
			'to_version'   => $to_version,
			'success'      => (bool) $success,
			'status'       => $status,
			'error'        => $error,
		);
	}

	/**
	 * Gives every result entry of a type the same documented keys, in a stable order.
	 *
	 * @param string                           $type    Update type.
	 * @param array<int, array<string, mixed>> $results Results.
	 * @return array<int, array<string, mixed>>
	 */
	private function normalize_results( $type, array $results ) {
		$defaults = array(
			'item'         => null,
			'name'         => null,
			'from_version' => null,
			'to_version'   => null,
			'success'      => false,
			'status'       => 'failed',
			'error'        => null,
		);
		switch ( $type ) {
			case 'plugin':
				$defaults += array(
					'active_before' => null,
					'active_after'  => null,
					'reactivated'   => null,
				);
				break;
			case 'theme':
				$defaults += array(
					'active_before' => null,
					'active_after'  => null,
				);
				break;
			case 'core':
				$defaults += array( 'db_upgrade_required' => false );
				break;
			case 'translations':
				$defaults += array(
					'language' => null,
					'updated'  => null,
				);
				break;
		}
		$defaults['messages'] = array();

		$out = array();
		foreach ( $results as $entry ) {
			$row = array();
			foreach ( $defaults as $key => $default ) {
				$row[ $key ] = array_key_exists( $key, $entry ) ? $entry[ $key ] : $default;
			}
			$out[] = $row;
		}
		return $out;
	}

	/**
	 * Orders results by the requested item order.
	 *
	 * @param string[]                            $items   Requested order.
	 * @param array<string, array<string, mixed>> $results Results keyed by item.
	 * @return array<string, array<string, mixed>>
	 */
	private function order_results( array $items, array $results ) {
		$ordered = array();
		foreach ( $items as $item ) {
			if ( isset( $results[ $item ] ) ) {
				$ordered[ $item ] = $results[ $item ];
			}
		}
		return $ordered + $results;
	}

	/**
	 * Sanitizes one requested item (plugin file, stylesheet, version, translation id).
	 *
	 * @param mixed $item Raw item.
	 * @return string
	 */
	public function sanitize_item( $item ) {
		if ( ! is_scalar( $item ) ) {
			return '';
		}
		$item = trim( sanitize_text_field( wp_unslash( (string) $item ) ) );
		return (string) preg_replace( '#[^A-Za-z0-9_\-./:@]#', '', $item );
	}

	/*
	 * ---------------------------------------------------------------------
	 * Health
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Cheap self-report used after updates.
	 *
	 * @return array<string, mixed>
	 */
	public function get_health() {
		global $wpdb;

		$last_error = error_get_last();
		$fatal      = array( E_ERROR, E_PARSE, E_CORE_ERROR, E_COMPILE_ERROR, E_USER_ERROR, E_RECOVERABLE_ERROR );
		if ( is_array( $last_error ) ) {
			$last_error = array(
				'type'    => (int) $last_error['type'],
				'fatal'   => in_array( (int) $last_error['type'], $fatal, true ),
				'message' => wp_strip_all_tags( (string) $last_error['message'] ),
				'file'    => $this->relative_path( (string) $last_error['file'] ),
				'line'    => (int) $last_error['line'],
			);
		}

		$db_ok = false;
		$db_error = null;
		$suppress = $wpdb->suppress_errors( true );
		// phpcs:ignore WordPress.DB.DirectDatabaseQuery.DirectQuery, WordPress.DB.DirectDatabaseQuery.NoCaching -- Liveness probe.
		$probe = $wpdb->get_var( 'SELECT 1' );
		$wpdb->suppress_errors( $suppress );
		if ( '1' === (string) $probe ) {
			$db_ok = true;
		} else {
			$db_error = $wpdb->last_error ? wp_strip_all_tags( $wpdb->last_error ) : __( 'Database probe returned no result.', 'website-manager-hermes-connector' );
		}

		$cron = $this->cron_health();

		$paused_plugins = array();
		if ( function_exists( 'wp_paused_plugins' ) ) {
			foreach ( (array) wp_paused_plugins()->get_all() as $plugin => $error ) {
				$paused_plugins[] = $this->paused_entry( $plugin, $error );
			}
		}
		$paused_themes = array();
		if ( function_exists( 'wp_paused_themes' ) ) {
			foreach ( (array) wp_paused_themes()->get_all() as $theme => $error ) {
				$paused_themes[] = $this->paused_entry( $theme, $error );
			}
		}

		$recovery    = function_exists( 'wp_is_recovery_mode' ) && wp_is_recovery_mode();
		$maintenance = file_exists( ABSPATH . '.maintenance' );

		$problems = array();
		if ( ! $db_ok ) {
			$problems[] = 'database_unreachable';
		}
		if ( $paused_plugins ) {
			$problems[] = 'paused_plugins';
		}
		if ( $paused_themes ) {
			$problems[] = 'paused_themes';
		}
		if ( $recovery ) {
			$problems[] = 'recovery_mode';
		}
		if ( $maintenance ) {
			$problems[] = 'maintenance_mode';
		}
		if ( is_array( $last_error ) && $last_error['fatal'] ) {
			$problems[] = 'fatal_error';
		}

		$warnings = array();
		if ( $cron['overdue_events'] > 0 ) {
			$warnings[] = 'cron_overdue';
		}

		if ( ! $db_ok || ( is_array( $last_error ) && $last_error['fatal'] ) ) {
			$status = 'critical';
		} elseif ( $problems ) {
			$status = 'degraded';
		} elseif ( $warnings ) {
			$status = 'warning';
		} else {
			$status = 'ok';
		}

		return array(
			'status'           => $status,
			'ok'               => 'ok' === $status || 'warning' === $status,
			'problems'         => $problems,
			'warnings'         => $warnings,
			'recovery_mode'    => $recovery,
			'maintenance_mode' => $maintenance,
			'last_error'       => $last_error,
			'database'         => array(
				'reachable' => $db_ok,
				'error'     => $db_error,
			),
			'cron'             => $cron,
			'paused_plugins'   => $paused_plugins,
			'paused_themes'    => $paused_themes,
			'wp_version'       => $this->wp_version(),
			'connector_version' => WMHC_VERSION,
			'checked_at'       => gmdate( 'c' ),
		);
	}

	/**
	 * WP-Cron health summary.
	 *
	 * @return array<string, mixed>
	 */
	private function cron_health() {
		$crons   = function_exists( '_get_cron_array' ) ? _get_cron_array() : array();
		$crons   = is_array( $crons ) ? $crons : array();
		$now     = time();
		$overdue = 0;
		$total   = 0;
		$next    = null;
		$oldest  = null;
		foreach ( $crons as $timestamp => $hooks ) {
			$count  = 0;
			foreach ( (array) $hooks as $events ) {
				$count += count( (array) $events );
			}
			$total += $count;
			if ( $timestamp < $now - self::CRON_OVERDUE_SECONDS ) {
				$overdue += $count;
				if ( null === $oldest || $timestamp < $oldest ) {
					$oldest = $timestamp;
				}
			}
			if ( $timestamp >= $now && ( null === $next || $timestamp < $next ) ) {
				$next = $timestamp;
			}
		}
		return array(
			'disabled'                => defined( 'DISABLE_WP_CRON' ) && DISABLE_WP_CRON,
			'alternate'               => defined( 'ALTERNATE_WP_CRON' ) && ALTERNATE_WP_CRON,
			'scheduled_events'        => $total,
			'overdue_events'          => $overdue,
			'overdue_threshold_s'     => self::CRON_OVERDUE_SECONDS,
			'oldest_overdue_at'       => $oldest ? gmdate( 'c', (int) $oldest ) : null,
			'next_event_at'           => $next ? gmdate( 'c', (int) $next ) : null,
			'doing_cron'              => (bool) get_transient( 'doing_cron' ),
		);
	}

	/**
	 * Paused extension entry.
	 *
	 * @param string $name  Plugin file / theme slug.
	 * @param mixed  $error Stored error array.
	 * @return array<string, mixed>
	 */
	private function paused_entry( $name, $error ) {
		$error = is_array( $error ) ? $error : array();
		return array(
			'name'    => (string) $name,
			'type'    => isset( $error['type'] ) ? (int) $error['type'] : null,
			'message' => isset( $error['message'] ) ? wp_strip_all_tags( (string) $error['message'] ) : null,
			'file'    => isset( $error['file'] ) ? $this->relative_path( (string) $error['file'] ) : null,
			'line'    => isset( $error['line'] ) ? (int) $error['line'] : null,
		);
	}

	/**
	 * Strips ABSPATH from a path.
	 *
	 * @param string $path Absolute path.
	 * @return string
	 */
	private function relative_path( $path ) {
		$abs = wp_normalize_path( ABSPATH );
		$p   = wp_normalize_path( $path );
		return 0 === strpos( $p, $abs ) ? substr( $p, strlen( $abs ) ) : basename( $p );
	}

	/*
	 * ---------------------------------------------------------------------
	 * Log
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Recent actions.
	 *
	 * @param int $limit Max entries (1-50).
	 * @return array<string, mixed>
	 */
	public function get_log( $limit = Action_Log::MAX_ENTRIES ) {
		$limit   = max( 1, min( Action_Log::MAX_ENTRIES, (int) $limit ) );
		$entries = $this->log->all( $limit );
		return array(
			'entries' => $entries,
			'count'   => count( $entries ),
			'max'     => Action_Log::MAX_ENTRIES,
		);
	}

	/*
	 * ---------------------------------------------------------------------
	 * Connection (admin page)
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Creates an application password for a user and returns the one-time
	 * connection string. The plain password is never stored or logged.
	 *
	 * @param \WP_User $user User.
	 * @return array{connection_string: string, uuid: string, name: string}|WP_Error
	 */
	public function create_connection( \WP_User $user ) {
		if ( ! class_exists( 'WP_Application_Passwords' ) || ! wp_is_application_passwords_available_for_user( $user ) ) {
			return new WP_Error( 'wmhc_app_passwords_unavailable', __( 'Application Passwords are not available for this user. They require HTTPS or a local environment, and may be disabled by a plugin.', 'website-manager-hermes-connector' ) );
		}
		if ( ! current_user_can( 'create_app_password', $user->ID ) ) {
			return new WP_Error( 'wmhc_forbidden', __( 'You are not allowed to create application passwords.', 'website-manager-hermes-connector' ) );
		}

		$name = self::APP_PASSWORD_NAME;
		if ( \WP_Application_Passwords::application_name_exists_for_user( $user->ID, $name ) ) {
			/* translators: %s: date and time (UTC). */
			$name = sprintf( __( 'Hermes (website-manager) %s', 'website-manager-hermes-connector' ), gmdate( 'Y-m-d H:i:s' ) );
		}

		$created = \WP_Application_Passwords::create_new_application_password( $user->ID, array( 'name' => $name ) );
		if ( is_wp_error( $created ) ) {
			return $created;
		}
		list( $password, $item ) = $created;

		$payload = array(
			'v'            => 1,
			'url'          => untrailingslashit( home_url() ),
			'user'         => $user->user_login,
			'app_password' => \WP_Application_Passwords::chunk_password( $password ),
			'rest_url'     => rest_url(),
		);

		$this->log->add( 'connection.create', array( $name ), 'success', array( 'source' => 'admin' ) );

		return array(
			'connection_string' => self::CONNECTION_PREFIX . base64_encode( wp_json_encode( $payload ) ), // phpcs:ignore WordPress.PHP.DiscouragedPHPFunctions.obfuscation_base64_encode -- Connection string format.
			'uuid'              => $item['uuid'],
			'name'              => $name,
		);
	}

	/**
	 * Existing Hermes application passwords for a user (no secrets).
	 *
	 * @param int $user_id User id.
	 * @return array<int, array<string, mixed>>
	 */
	public function list_connections( $user_id ) {
		if ( ! class_exists( 'WP_Application_Passwords' ) ) {
			return array();
		}
		$out = array();
		foreach ( \WP_Application_Passwords::get_user_application_passwords( $user_id ) as $item ) {
			if ( 0 !== strpos( $item['name'], 'Hermes (website-manager)' ) ) {
				continue;
			}
			$out[] = array(
				'name'      => $item['name'],
				'created'   => (int) $item['created'],
				'last_used' => $item['last_used'] ? (int) $item['last_used'] : null,
			);
		}
		return $out;
	}

	/**
	 * Requirements check for the admin page.
	 *
	 * @return array<string, array{ok: bool, label: string, detail: string}>
	 */
	public function requirements() {
		$this->load_admin_includes();
		$https     = is_ssl() || wp_is_using_https();
		$local     = 'local' === wp_get_environment_type();
		$app_pw    = wp_is_application_passwords_available();
		$file_mods = wp_is_file_mod_allowed( 'wmhc_run_updates' );
		$fs_method = get_filesystem_method();
		$pretty    = (bool) get_option( 'permalink_structure' );

		return array(
			'https'                 => array(
				'ok'     => $https || $local,
				'label'  => __( 'HTTPS', 'website-manager-hermes-connector' ),
				'detail' => $https ? __( 'The site uses HTTPS.', 'website-manager-hermes-connector' ) : ( $local ? __( 'No HTTPS, but this is a local environment.', 'website-manager-hermes-connector' ) : __( 'The site does not use HTTPS. Application Passwords need HTTPS.', 'website-manager-hermes-connector' ) ),
			),
			'application_passwords' => array(
				'ok'     => $app_pw,
				'label'  => __( 'Application Passwords', 'website-manager-hermes-connector' ),
				'detail' => $app_pw ? __( 'Available.', 'website-manager-hermes-connector' ) : __( 'Not available (needs HTTPS or a local environment, or disabled by a plugin).', 'website-manager-hermes-connector' ),
			),
			'rest_api'              => array(
				'ok'     => true,
				'label'  => __( 'REST API', 'website-manager-hermes-connector' ),
				'detail' => $pretty ? rest_url( WMHC_REST_NAMESPACE ) : __( 'Plain permalinks: Hermes must use the ?rest_route= form of the REST URL.', 'website-manager-hermes-connector' ),
			),
			'file_mods'             => array(
				'ok'     => $file_mods && 'direct' === $fs_method,
				'label'  => __( 'Updates possible', 'website-manager-hermes-connector' ),
				'detail' => ! $file_mods ? __( 'File modifications are disabled (DISALLOW_FILE_MODS).', 'website-manager-hermes-connector' ) : sprintf( /* translators: %s: filesystem method */ __( 'Filesystem method: %s (updates without prompts need "direct" or stored FTP credentials).', 'website-manager-hermes-connector' ), $fs_method ),
			),
		);
	}

	/*
	 * ---------------------------------------------------------------------
	 * Helpers
	 * ---------------------------------------------------------------------
	 */

	/**
	 * Loads the admin includes needed for update data and upgraders.
	 *
	 * @return void
	 */
	public function load_admin_includes() {
		require_once ABSPATH . 'wp-admin/includes/admin.php';
		require_once ABSPATH . 'wp-admin/includes/class-wp-upgrader.php';
	}

	/**
	 * Loads the upgrader skin.
	 *
	 * @return void
	 */
	private function load_upgrader() {
		$this->load_admin_includes();
		require_once WMHC_DIR . 'includes/class-upgrader-skin.php';
	}

	/**
	 * Current WordPress version.
	 *
	 * @param bool $from_disk Read wp-includes/version.php instead of the loaded global (after a core update).
	 * @return string
	 */
	private function wp_version( $from_disk = false ) {
		if ( $from_disk ) {
			$file = ABSPATH . WPINC . '/version.php';
			// phpcs:ignore WordPress.WP.AlternativeFunctions.file_get_contents_file_get_contents -- Local file.
			$code = file_exists( $file ) ? (string) file_get_contents( $file ) : '';
			if ( preg_match( '/\$wp_version\s*=\s*\'([^\']+)\'/', $code, $m ) ) {
				return $m[1];
			}
		}
		return function_exists( 'wp_get_wp_version' ) ? wp_get_wp_version() : (string) get_bloginfo( 'version' );
	}

	/**
	 * Whether the DB needs an upgrade after a core update.
	 *
	 * @return bool
	 */
	private function db_upgrade_required() {
		$file = ABSPATH . WPINC . '/version.php';
		// phpcs:ignore WordPress.WP.AlternativeFunctions.file_get_contents_file_get_contents -- Local file.
		$code = file_exists( $file ) ? (string) file_get_contents( $file ) : '';
		if ( preg_match( '/\$wp_db_version\s*=\s*(\d+)/', $code, $m ) ) {
			return (int) get_option( 'db_version' ) !== (int) $m[1];
		}
		return false;
	}

	/**
	 * Whether moving from $from to $to changes the x.y branch.
	 *
	 * @param string $from Current version.
	 * @param string $to   Target version.
	 * @return bool
	 */
	private function is_major_core_update( $from, $to ) {
		$branch = static function ( $version ) {
			$parts = explode( '.', preg_replace( '/[^0-9.].*$/', '', (string) $version ) );
			return ( isset( $parts[0] ) ? (int) $parts[0] : 0 ) . '.' . ( isset( $parts[1] ) ? (int) $parts[1] : 0 );
		};
		return $branch( $from ) !== $branch( $to );
	}
}
