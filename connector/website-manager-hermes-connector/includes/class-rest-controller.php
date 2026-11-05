<?php
/**
 * REST routes under website-manager-hermes/v1.
 *
 * @package WebsiteManagerHermesConnector
 */

namespace WebsiteManagerHermesConnector;

use WP_Error;
use WP_REST_Request;
use WP_REST_Server;

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Registers and serves the connector REST routes.
 */
class Rest_Controller {

	/**
	 * Shared service.
	 *
	 * @var Service
	 */
	private $service;

	/**
	 * Constructor.
	 *
	 * @param Service $service Service.
	 */
	public function __construct( Service $service ) {
		$this->service = $service;
	}

	/**
	 * Registers routes.
	 *
	 * @return void
	 */
	public function register_routes() {
		register_rest_route(
			WMHC_REST_NAMESPACE,
			'/status',
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => array( $this, 'get_status' ),
				'permission_callback' => array( $this, 'can_view' ),
			)
		);

		register_rest_route(
			WMHC_REST_NAMESPACE,
			'/updates',
			array(
				array(
					'methods'             => WP_REST_Server::READABLE,
					'callback'            => array( $this, 'get_updates' ),
					'permission_callback' => array( $this, 'can_view' ),
					'args'                => array(
						'refresh' => array(
							'description' => __( 'Force a fresh update check against WordPress.org.', 'website-manager-hermes-connector' ),
							'type'        => 'boolean',
							'default'     => false,
						),
					),
				),
				array(
					'methods'             => WP_REST_Server::CREATABLE,
					'callback'            => array( $this, 'run_updates' ),
					'permission_callback' => array( $this, 'can_update' ),
					'args'                => array(
						'type'  => array(
							'description' => __( 'Update type.', 'website-manager-hermes-connector' ),
							'type'        => 'string',
							'enum'        => Service::UPDATE_TYPES,
							'required'    => true,
						),
						'items' => array(
							'description' => __( 'Plugin files, theme stylesheets, a core version, or translation ids. Empty means all of that type.', 'website-manager-hermes-connector' ),
							'type'        => 'array',
							'items'       => array( 'type' => 'string' ),
							'default'     => array(),
						),
					),
				),
			)
		);

		register_rest_route(
			WMHC_REST_NAMESPACE,
			'/health',
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => array( $this, 'get_health' ),
				'permission_callback' => array( $this, 'can_view' ),
			)
		);

		register_rest_route(
			WMHC_REST_NAMESPACE,
			'/log',
			array(
				'methods'             => WP_REST_Server::READABLE,
				'callback'            => array( $this, 'get_log' ),
				'permission_callback' => array( $this, 'can_view' ),
				'args'                => array(
					'limit' => array(
						'type'    => 'integer',
						'minimum' => 1,
						'maximum' => Action_Log::MAX_ENTRIES,
						'default' => Action_Log::MAX_ENTRIES,
					),
				),
			)
		);
	}

	/**
	 * Permission: read routes.
	 *
	 * @return true|WP_Error
	 */
	public function can_view() {
		if ( $this->service->current_user_can_view() ) {
			return true;
		}
		return $this->forbidden();
	}

	/**
	 * Permission: POST /updates (per-type capability).
	 *
	 * @param WP_REST_Request $request Request.
	 * @return true|WP_Error
	 */
	public function can_update( WP_REST_Request $request ) {
		$type = (string) $request->get_param( 'type' );
		if ( ! in_array( $type, Service::UPDATE_TYPES, true ) ) {
			// Let argument validation report the bad type for authenticated users.
			return is_user_logged_in() && $this->service->current_user_can_view() ? true : $this->forbidden();
		}
		if ( $this->service->current_user_can_update( $type ) ) {
			return true;
		}
		return $this->forbidden();
	}

	/**
	 * Standard forbidden error.
	 *
	 * @return WP_Error
	 */
	private function forbidden() {
		return new WP_Error(
			'rest_forbidden',
			__( 'Sorry, you are not allowed to do that.', 'website-manager-hermes-connector' ),
			array( 'status' => rest_authorization_required_code() )
		);
	}

	/**
	 * GET /status.
	 *
	 * @return \WP_REST_Response
	 */
	public function get_status() {
		return rest_ensure_response( $this->service->get_status() );
	}

	/**
	 * GET /updates.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return \WP_REST_Response
	 */
	public function get_updates( WP_REST_Request $request ) {
		return rest_ensure_response( $this->service->get_updates( (bool) $request->get_param( 'refresh' ) ) );
	}

	/**
	 * POST /updates.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return \WP_REST_Response|WP_Error
	 */
	public function run_updates( WP_REST_Request $request ) {
		$items = $request->get_param( 'items' );
		$items = is_array( $items ) ? $items : array();
		$result = $this->service->run_updates( (string) $request->get_param( 'type' ), $items, 'rest' );
		if ( is_wp_error( $result ) ) {
			return $result;
		}
		return rest_ensure_response( $result );
	}

	/**
	 * GET /health.
	 *
	 * @return \WP_REST_Response
	 */
	public function get_health() {
		return rest_ensure_response( $this->service->get_health() );
	}

	/**
	 * GET /log.
	 *
	 * @param WP_REST_Request $request Request.
	 * @return \WP_REST_Response
	 */
	public function get_log( WP_REST_Request $request ) {
		return rest_ensure_response( $this->service->get_log( (int) $request->get_param( 'limit' ) ) );
	}
}
